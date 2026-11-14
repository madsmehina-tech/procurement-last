# -*- coding: utf-8 -*-
"""v28.0 - the charts + filters panel: its rows per page, the filter (the Excel export holds exactly what the screen shows), and where it is shown."""
import io
from datetime import datetime

import pytest

import conftest
import fake_db
import helpers as h
import insights
import seed


@pytest.fixture(autouse=True)
def _schema():
    conftest.build_schema()
    seed.seed(n_subs=3, certs_per_sub=4, n_orders=12)


@pytest.fixture()
def client():
    import app as appmod
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    return c


def _json(client, page):
    r = client.get("/insights/%s/data.json" % page)
    assert r.status_code == 200, (page, r.status_code)
    return r.get_json()


def test_every_page_has_rows_in_one_shape_and_amounts_in_riyals(client):
    for page in insights.PAGES:
        d = _json(client, page)
        assert d["meta"]["page"] == page and d["meta"]["title"] and d["meta"]["party_label"]
        for r in d["rows"]:
            assert set(r) >= {"kind", "no", "date", "project", "party", "amount", "status", "href"} and isinstance(r["amount"], (int, float))
            assert r["date"] == "" or len(r["date"]) == 10
    home = _json(client, "home")["rows"]
    assert {r["kind"] for r in home} == {"po", "cert"} and len([r for r in home if r["kind"] == "po"]) == 12 and len([r for r in home if r["kind"] == "cert"]) == 12
    assert round(sum(r["amount"] for r in home if r["kind"] == "po"), 2) == round(sum(o["TotalAmount"] for o in fake_db.query("SELECT [TotalAmount] FROM [PurchaseOrders]")), 2)
    assert {r["party_kind"] for r in home if r["kind"] == "cert"} == {"مقاول"}
    assert _json(client, "items")["rows"] and all(r["kind"] == "item" for r in _json(client, "items")["rows"])


def test_a_foreign_currency_order_counts_by_its_riyal_equivalent(client):
    fake_db.execute("INSERT INTO [PurchaseOrders] ([PONumber], [OrderDate], [SupplierName], [Project], [TotalAmount], [TotalCurrency], [CurrencyCode], [ExchangeRate], [PaymentStatus], [CreatedAt]) "
                    "VALUES ('PO-USD', ?, 'مورد دولي', 'مشروع 1', 3750, 1000, 'USD', 3.75, 'معلق', ?)", (datetime(2026, 5, 2), datetime.now()))
    row = [r for r in _json(client, "orders")["rows"] if r["no"] == "PO-USD"][0]
    assert row["amount"] == 3750.0 and row["date"] == "2026-05-02"


def test_the_filter_rules():
    rows = [{"kind": "po", "no": "PO-1", "date": "2026-03-10", "project": "أ", "party": "مصنع اليمامية", "amount": 10.0, "status": "معلق"},
            {"kind": "po", "no": "PO-2", "date": "2026-05-01", "project": "ب", "party": "شركة الوسائل", "amount": 20.0, "status": "تم السداد"},
            {"kind": "cert", "no": "مستخلص 3", "date": "2026-05-20", "project": "أ", "party": "مؤسسة الظل", "amount": 30.0, "status": "مصروف"},
            {"kind": "pr", "no": "PR-0001", "date": "", "project": "أ", "party": "مصنع اليمامية", "amount": 5.0, "status": "مسودة"}]
    nos = lambda **a: [r["no"] for r in insights.apply(rows, a)]
    assert nos() == ["PO-1", "PO-2", "مستخلص 3", "PR-0001"]
    assert nos(kind="po") == ["PO-1", "PO-2"] and nos(project="أ", kind="cert") == ["مستخلص 3"]
    assert nos(**{"from": "2026-05"}) == ["PO-2", "مستخلص 3"] and nos(**{"from": "2026-03", "to": "2026-04"}) == ["PO-1"]      # a row without a date is outside any month range
    assert nos(party="مصنع اليمامية") == ["PO-1", "PR-0001"] and nos(status="تم السداد") == ["PO-2"]
    assert nos(q="اليمامية") == ["PO-1", "PR-0001"] and nos(q="اليماميه po-1") == ["PO-1"] and nos(q="لا يوجد") == []
    assert insights.summary(insights.apply(rows, {"kind": "po"})) == {"count": 2, "total": 30.0, "average": 15.0, "parties": 2, "projects": 2}


def test_the_excel_export_holds_exactly_the_filtered_rows_as_a_plain_table(client):
    from openpyxl import load_workbook
    rows = _json(client, "orders")["rows"]
    project = rows[0]["project"]
    expect = [r for r in rows if r["project"] == project]
    r = client.get("/insights/orders/export.xlsx", query_string={"project": project})
    assert r.status_code == 200 and "spreadsheetml" in r.headers["Content-Type"]
    wb = load_workbook(io.BytesIO(r.data))
    ws = wb["Data"]
    data = list(ws.iter_rows(values_only=True))
    assert data[0][1] == "الرقم" and data[0][7] == "المبلغ بالريال" and len(data) - 1 == len(expect)
    assert sorted(x[1] for x in data[1:]) == sorted(e["no"] for e in expect) and all(x[4] == project for x in data[1:])
    assert isinstance(data[1][2], datetime) and isinstance(data[1][7], (int, float))                    # real dates and numbers: Power BI reads them as such
    assert round(sum(x[7] for x in data[1:]), 2) == round(sum(e["amount"] for e in expect), 2)
    info = dict((a, b) for a, b in wb["Filters"].iter_rows(values_only=True))
    assert info["المشروع"] == project and info["عدد السجلات"] == len(expect)


def test_a_formula_typed_as_a_supplier_name_stays_text_in_the_export(client):
    from openpyxl import load_workbook
    fake_db.execute("INSERT INTO [PurchaseOrders] ([PONumber], [OrderDate], [SupplierName], [Project], [TotalAmount], [PaymentStatus], [CreatedAt]) VALUES ('PO-X', ?, '=HYPERLINK(\"http://x\")', 'م', 1, 'معلق', ?)", (datetime.now(), datetime.now()))
    wb = load_workbook(io.BytesIO(client.get("/insights/orders/export.xlsx", query_string={"q": "PO-X"}).data))
    cell = [c for c in wb["Data"]["F"] if c.value and "HYPERLINK" in str(c.value)][0]
    assert cell.data_type == "s"


def test_the_panel_is_on_the_home_page_and_on_the_ten_main_pages(client):
    pages = {"/": "home", "/orders": "orders", "/suppliers": "suppliers", "/certificates": "certificates", "/subcontractors": "subcontractors", "/payments": "payments",
             "/vouchers": "vouchers", "/cycles/all": "cycles", "/items": "items", "/archive": "archive", "/compare": "compare"}
    for url, key in pages.items():
        html = client.get(url).get_data(as_text=True)
        assert 'data-insights="%s"' % key in html, url
        assert "chart.umd.js" in html and "insights.js" in html and "تصدير Excel" in html and "/insights/%s/export.xlsx" % key in html
    assert client.get("/static/chart.umd.js").status_code == 200 and client.get("/static/insights.js").status_code == 200          # shipped with the system: no internet needed


def test_the_voucher_table_follows_the_filters_by_its_row_keys(client):
    s = fake_db.execute_insert("INSERT INTO [Suppliers] ([Name], [CreatedAt]) VALUES ('م', ?)", (datetime.now(),))
    from test_voucher_posting import _form
    client.post("/vouchers/new", data=_form("sup", s, 100, choice="no"))
    html = client.get("/vouchers").get_data(as_text=True)
    assert 'data-link="1"' in html and 'data-ins-key="PR-0001"' in html
    assert [r["no"] for r in _json(client, "vouchers")["rows"]] == ["PR-0001"]


def test_the_two_old_dashboards_are_tabs_of_the_home_page_not_menu_entries(client):
    home = client.get("/").get_data(as_text=True)
    assert "نظرة عامة" in home and "مؤشرات المشتريات" in home and "متابعة مقاولي الباطن" in home
    assert home.count('href="/orders/analytics"') == 1 and home.count('href="/subcontractors/dashboard"') == 1          # only in the tabs
    for url in ("/orders/analytics", "/subcontractors/dashboard"):
        page = client.get(url).get_data(as_text=True)
        assert 'class="home-tabs"' in page and "نظرة عامة" in page


def test_a_person_without_the_permission_gets_no_data(client):
    import auth
    role = fake_db.execute_insert("INSERT INTO [Roles] ([RoleKey], [RoleName], [Description], [IsSystem]) VALUES ('nobody', 'x', 'x', 0)")
    uid = fake_db.execute_insert("INSERT INTO [Users] ([Username], [PasswordHash], [FullName], [RoleID], [Status], [CreatedAt]) VALUES ('u9', 'x', 'u9', ?, 'active', ?)", (role, datetime.now()))
    auth.invalidate_roles()
    import app as appmod
    c2 = appmod.app.test_client()
    with c2.session_transaction() as sess:
        sess["user_id"] = uid
    assert c2.get("/insights/orders/data.json").status_code == 403 and c2.get("/insights/orders/export.xlsx").status_code == 403
    assert c2.get("/insights/nope/data.json").status_code == 404

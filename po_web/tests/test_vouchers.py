# -*- coding: utf-8 -*-
"""The general payment voucher (أمر صرف عام): the money rules, numbering, the linked lists, the printed form, and its permissions."""
from datetime import datetime

import pytest

import conftest
import fake_db
import helpers as h
import voucher_service as vs


@pytest.fixture(autouse=True)
def _schema():
    conftest.build_schema()


@pytest.fixture()
def client():
    import app as appmod
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    return c


def _project(name="الفرسان"):
    return h.project_id(name)


def _supplier(name="مؤسسة ملتقى الفنون"):
    return fake_db.execute_insert("INSERT INTO [Suppliers] ([Name], [CreatedAt]) VALUES (?, ?)", (name, datetime.now()))


def _form(project_id, party_id, kind="sup", **over):
    f = {"voucher_date": "2026-10-24", "project_id": str(project_id), "party_kind": kind, "party_id": str(party_id), "request_type": "أعمال زراعة", "department": "العقود والمشتريات (C&P)",
         "pay_method": "تحويل بنكي", "pay_statement": "بيان السداد", "description": "سداد مستخلص أعمال زراعة", "amount_mode": "incl", "vat_pct": "15", "discount": "",
         "item_desc": ["سداد مستخلص أعمال زراعة"], "item_qty": ["1"], "item_price": ["144293.39"], "ref_type": ["فاتورة", "مستخلص"], "ref_no": ["501", "2"]}
    f.update(over)
    return f


# ------------------------------------------------------------------------------------------------ the money (the numbers on the company's sample form)
def test_prices_with_vat_split_exactly_like_the_sample_voucher():
    t = vs.compute([(1, 144293.39)], "incl", 15)
    assert (t["subtotal"], t["vat"], t["total"], t["net"]) == (125472.51, 18820.88, 144293.39, 144293.39)


def test_prices_without_vat_add_it_and_reach_the_same_total():
    t = vs.compute([(1, 125472.51)], "excl", 15)
    assert (t["subtotal"], t["vat"], t["total"]) == (125472.51, 18820.88, 144293.39)


def test_the_discount_comes_off_after_the_vat():
    t = vs.compute([(2, 1000)], "excl", 15, 300)
    assert t["total"] == 2300 and t["net"] == 2000
    assert vs.compute([(1, 1000)], "excl", 0)["vat"] == 0


# ------------------------------------------------------------------------------------------------ numbering
def test_numbering_continues_from_the_old_system_and_cannot_be_changed_later(client):
    vs.set_start_number(27)
    pid, sid = _project(), _supplier()
    client.post("/vouchers/new", data=_form(pid, sid))
    client.post("/vouchers/new", data=_form(pid, sid))
    assert [r["VoucherNo"] for r in fake_db.query("SELECT [VoucherNo] FROM [PaymentVouchers] ORDER BY [VoucherID]")] == ["PR-0027", "PR-0028"]
    with pytest.raises(vs.VoucherError):
        vs.set_start_number(100)


# ------------------------------------------------------------------------------------------------ the form
def test_the_voucher_page_has_the_cascading_form(client):
    html = client.get("/vouchers/new").get_data(as_text=True)
    for needle in ("اسم المشروع", "المستفيد هو", "اسم المستفيد", "بنود الطلب", "المستندات المرجعية", "/vouchers/api/parties", "/vouchers/api/documents"):
        assert needle in html, needle


def test_creating_a_voucher_stores_the_figures_and_the_print_shows_the_company_form(client):
    pid, sid = _project(), _supplier()
    r = client.post("/vouchers/new", data=_form(pid, sid, discount="", sign_contracts="م محمود عدس", sign_projects="م محمد الجداوى", sign_accounts="محمد الفرماوي",
                                                sign_finance="محمد البشير", sign_gm="صنهات العتيبي", department="إدارة العقود والمشتريات"), follow_redirects=False)
    assert r.status_code == 302
    v = fake_db.query("SELECT * FROM [PaymentVouchers]")[0]
    assert v["Status"] == "مسودة" and v["Net"] == 144293.39 and v["VatAmount"] == 18820.88 and v["PartyName"] == "مؤسسة ملتقى الفنون" and v["CostCenter"] == "الفرسان"
    html = client.get("/vouchers/%d/print" % v["VoucherID"]).get_data(as_text=True)
    for needle in ("طلب صرف", v["VoucherNo"], "2026-10-24", "مؤسسة ملتقى الفنون", "أعمال زراعة", "تحويل بنكي", "مركز التكلفة", "بنود الطلب", "144,293.39",
                   "المبلغ بالحروف", "فقط مائة وأربعة وأربعون ألفاً", "لا غير", "إدارة العقود والمشتريات", "تاريخ ووقت الطباعة",
                   "مدير العقود والمشتريات", "م محمود عدس", "مدير عام المشاريع", "م محمد الجداوى", "مدير الحسابات", "المدير المالي", "المدير العام"):
        assert needle in html, needle
    for gone in ("المحاسب", "المستندات المرجعية", "بيان السداد", "القسم الطالب", "الإجمالي قبل الخصم", "الحالة:"):          # the simplified company form
        assert gone not in html, gone
    assert html.index("مدير العقود والمشتريات") < html.index("مدير عام المشاريع") < html.index("مدير الحسابات") < html.index("المدير المالي") < html.index("المدير العام")


def test_the_totals_box_is_printed_only_when_asked(client):
    pid, sid = _project(), _supplier()
    client.post("/vouchers/new", data=_form(pid, sid, show_totals="1"))
    vid = fake_db.scalar("SELECT [VoucherID] FROM [PaymentVouchers]")
    html = client.get("/vouchers/%d/print" % vid).get_data(as_text=True)
    assert "الإجمالي قبل الخصم" in html and "125,472.51" in html and "18,820.88" in html and "صافي قيمة الطلب" in html


def test_mistakes_are_reported_and_nothing_typed_is_lost(client):
    pid, sid = _project(), _supplier()
    r = client.post("/vouchers/new", data=_form(pid, sid, item_price=["abc"], description=""))
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "السعر في البند 1 غير صالح" in html and "البيان مطلوب" in html
    assert fake_db.scalar("SELECT COUNT(*) FROM [PaymentVouchers]") == 0
    assert 'value="2026-10-24"' in html                                               # the form comes back filled
    bad = client.post("/vouchers/new", data=_form("", "", item_price=[""], item_desc=[""]))
    text = bad.get_data(as_text=True)
    assert "اختر اسم المشروع" in text and "اختر المستفيد" in text and "أضف بنداً واحداً" in text


def test_a_discount_bigger_than_the_total_is_refused(client):
    pid, sid = _project(), _supplier()
    r = client.post("/vouchers/new", data=_form(pid, sid, item_price=["100"], discount="500"))
    assert "أكبر من الإجمالي" in r.get_data(as_text=True) and fake_db.scalar("SELECT COUNT(*) FROM [PaymentVouchers]") == 0


def test_draft_can_be_edited_then_approved_then_it_is_locked(client):
    pid, sid = _project(), _supplier()
    client.post("/vouchers/new", data=_form(pid, sid))
    vid = fake_db.scalar("SELECT [VoucherID] FROM [PaymentVouchers]")
    assert client.get("/vouchers/%d/edit" % vid).status_code == 200
    client.post("/vouchers/%d/edit" % vid, data=_form(pid, sid, item_price=["200"], description="بيان معدّل"))
    assert fake_db.scalar("SELECT [Net] FROM [PaymentVouchers]") == 200 and fake_db.scalar("SELECT [Description] FROM [PaymentVouchers]") == "بيان معدّل"
    client.post("/vouchers/%d/approve" % vid)
    assert fake_db.scalar("SELECT [Status] FROM [PaymentVouchers]") == "معتمد"
    client.post("/vouchers/%d/edit" % vid, data=_form(pid, sid, item_price=["999"]))
    assert fake_db.scalar("SELECT [Net] FROM [PaymentVouchers]") == 200                  # locked
    client.post("/vouchers/%d/cancel" % vid)
    assert fake_db.scalar("SELECT [Status] FROM [PaymentVouchers]") == "ملغي"
    assert "ملغي" in client.get("/vouchers/%d/print" % vid).get_data(as_text=True)
    assert fake_db.scalar("SELECT COUNT(*) FROM [PaymentVouchers]") == 1               # cancelled, never deleted


def test_a_voucher_never_posts_to_a_contractors_account(client):
    pid, s = _project(), h.add_sub()
    before = fake_db.scalar("SELECT COUNT(*) FROM [LedgerEntries]", default=0)
    client.post("/vouchers/new", data=_form(pid, s, kind="sub"))
    assert fake_db.scalar("SELECT COUNT(*) FROM [LedgerEntries]", default=0) == before


# ------------------------------------------------------------------------------------------------ the linked lists
def test_parties_related_to_the_project_come_first(client):
    pid = _project("الفرسان")
    a, b = _supplier("مورد الأول"), _supplier("مورد الثاني")
    fake_db.execute("INSERT INTO [PurchaseOrders] ([PONumber], [SupplierID], [SupplierName], [Project], [TotalAmount], [CreatedAt]) VALUES ('PO-1', ?, 'مورد الثاني', 'الفرسان', 100, ?)", (b, datetime.now()))
    rows = client.get("/vouchers/api/parties?kind=sup&project=الفرسان").get_json()
    assert rows[0]["name"] == "مورد الثاني" and rows[0]["related"] and not rows[1]["related"]
    contractor = h.add_sub("مقاول الفرسان")
    h.add_cert(contractor, 1000)                                                         # on «مشروع 1»
    rows = client.get("/vouchers/api/parties?kind=sub&project=مشروع 1").get_json()
    assert rows[0]["name"] == "مقاول الفرسان" and rows[0]["related"]


def test_documents_of_a_contractor_and_of_a_supplier_are_pulled_in_with_their_amounts(client):
    s = h.add_sub()
    paid = h.add_cert(s, 5000, no=1)
    open_cert = h.add_cert(s, 144293.39, no=2)
    fake_db.execute_insert("INSERT INTO [Payments] ([CertificateID], [Amount], [PaidAt]) VALUES (?, ?, ?)", (paid, 5000, datetime.now()))
    docs = client.get("/vouchers/api/documents?kind=sub&id=%d&project=مشروع 1" % s).get_json()
    assert [d["no"] for d in docs] == ["2"] and docs[0]["amount"] == 144293.39 and docs[0]["type"] == "مستخلص"
    sid = _supplier()
    fake_db.execute("INSERT INTO [PurchaseOrders] ([PONumber], [SupplierID], [SupplierName], [Project], [TotalAmount], [PaymentStatus], [CreatedAt]) VALUES ('PO-7', ?, 'x', 'برج', 900, 'معلق', ?)", (sid, datetime.now()))
    fake_db.execute("INSERT INTO [PurchaseOrders] ([PONumber], [SupplierID], [SupplierName], [Project], [TotalAmount], [PaymentStatus], [CreatedAt]) VALUES ('PO-8', ?, 'x', 'برج', 500, 'تم السداد', ?)", (sid, datetime.now()))
    docs = client.get("/vouchers/api/documents?kind=sup&id=%d&project=برج" % sid).get_json()
    assert [d["no"] for d in docs] == ["PO-7"]                                           # the paid one is not offered
    assert client.get("/vouchers/api/documents?kind=sup&id=abc").get_json() == []


# ------------------------------------------------------------------------------------------------ permissions and the menu
def test_the_print_tab_is_in_the_menu_and_the_list_page_has_the_button(client):
    html = client.get("/vouchers").get_data(as_text=True)
    assert "إنشاء أمر صرف عام" in html and "الطباعة" in html and "أوامر الصرف العامة" in client.get("/").get_data(as_text=True)


def test_a_person_without_the_permission_cannot_open_or_create(client):
    import auth
    from datetime import datetime as dt
    rid = fake_db.execute_insert("INSERT INTO [Roles] ([RoleKey], [RoleName], [Description], [IsSystem]) VALUES ('nobody', 'x', 'x', 0)")
    uid = fake_db.execute_insert("INSERT INTO [Users] ([Username], [PasswordHash], [FullName], [RoleID], [Status], [CreatedAt]) VALUES ('n1', 'x', 'n', ?, 'active', ?)", (rid, dt.now()))
    auth.invalidate_roles()
    import app as appmod
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = uid
    assert c.get("/vouchers").status_code == 403 and c.get("/vouchers/new").status_code == 403
    assert c.post("/vouchers/new", data={}).status_code == 403
    assert c.get("/settings/opening-balances").status_code == 403

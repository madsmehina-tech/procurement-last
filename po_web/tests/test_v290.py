# -*- coding: utf-8 -*-
"""v29.0 - the look of the system, the print settings (watermark, QR + verification code), the supplier rating and the alerts."""
import re
from datetime import datetime, timedelta

import pytest

import alerts
import conftest
import fake_db
import print_settings
import supplier_rating
import ui_theme


@pytest.fixture(autouse=True)
def _schema():
    conftest.build_schema()
    ui_theme.reset_cache()
    print_settings.reset_cache()
    alerts.clear_cache()
    yield
    ui_theme.reset_cache()
    print_settings.reset_cache()
    alerts.clear_cache()


@pytest.fixture()
def client():
    import app as appmod
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    return c


def _user(role_key, username):
    import auth
    rid = fake_db.scalar("SELECT [RoleID] FROM [Roles] WHERE [RoleKey] = ?", (role_key,))
    uid = fake_db.execute_insert("INSERT INTO [Users] ([Username], [PasswordHash], [FullName], [RoleID], [Status], [CreatedAt]) VALUES (?, 'x', ?, ?, 'active', ?)",
                                 (username, username, rid, datetime.now()))
    auth.invalidate_roles()
    import app as appmod
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = uid
    return uid, c


def _order(po, supplier="مورد أ", agreed=None, receipt="لم يبدأ", got=None, quality=None, items=(("حديد", 1, 100.0),), rate=1, **extra):
    sid = fake_db.scalar("SELECT [SupplierID] FROM [Suppliers] WHERE [Name] = ?", (supplier,)) or \
        fake_db.execute_insert("INSERT INTO [Suppliers] ([Name], [CreatedAt]) VALUES (?, ?)", (supplier, datetime.now()))
    cols = {"PONumber": po, "OrderDate": datetime(2026, 10, 1), "SupplierName": supplier, "SupplierID": sid, "Project": "م", "TotalAmount": 115.0,
            "ReceiptStatus": receipt, "InvoiceStatus": "معلق", "PaymentStatus": "معلق", "AgreedDeliveryDate": agreed, "ReceiptDate": got,
            "ReceiptQuality": quality, "ExchangeRate": rate, "VatMode": "add", "VatPct": 15, "CreatedAt": datetime.now()}
    cols.update(extra)
    fake_db.execute("INSERT INTO [PurchaseOrders] (%s) VALUES (%s)" % (", ".join("[%s]" % k for k in cols), ", ".join("?" for _ in cols)), tuple(cols.values()))
    for i, (name, qty, price) in enumerate(items, 1):
        fake_db.execute("INSERT INTO [OrderItems] ([PONumber], [LineNo], [ItemName], [Unit], [Qty], [UnitPrice], [QtyReceived]) VALUES (?, ?, ?, 'عدد', ?, ?, 0)", (po, i, name, qty, price))
    return sid


# ===================================================================== 1. the look
def test_the_default_look_adds_nothing_and_a_choice_reaches_every_page(client):
    assert ui_theme.css() == "" and ui_theme.get() == ui_theme.DEFAULT
    assert "theme-css" not in client.get("/orders").get_data(as_text=True)
    r = client.post("/settings/appearance", data={"preset": "blue", "font": "tajawal", "size": "large", "density": "compact"})
    assert r.status_code == 302
    for url in ("/", "/orders", "/suppliers"):
        html = client.get(url).get_data(as_text=True)
        assert 'id="theme-css"' in html and "#1D4E89" in html and '"Tajawal"' in html and "--ui-zoom: 1.1" in html and "--row-pad: 8px" in html and "fonts-extra.css" in html
    client.post("/settings/appearance", data={"reset": "1"})
    assert "theme-css" not in client.get("/orders").get_data(as_text=True)


def test_unknown_values_fall_back_and_a_too_light_brand_colour_is_made_readable():
    assert ui_theme.clean({"preset": "pink", "font": "comic", "size": "huge", "brand": "red"}) == ui_theme.DEFAULT
    assert ui_theme.clean({"preset": "brand"})["preset"] == "green"                          # «ألوان الشعار» without a colour
    t = ui_theme.tokens({"preset": "brand", "brand": "#F5D76E"})
    assert ui_theme.contrast(t["--accent"], "#FFFFFF") >= 4.5 and t["--accent"] != "#F5D76E"
    for key in ui_theme.PRESETS:                                                             # every ready-made template keeps white text readable on its buttons
        if key != "brand":
            assert ui_theme.contrast(ui_theme.tokens({"preset": key})["--accent"], "#FFFFFF") >= 4.5


def test_only_the_system_administrator_changes_the_look(client):
    _uid, c = _user("procurement", "buyer1")
    assert c.get("/settings/appearance").status_code == 403 and c.post("/settings/appearance", data={"preset": "grey"}).status_code == 403
    assert client.get("/settings/appearance/tokens", query_string={"preset": "earth"}).get_json()["tokens"]["--accent"] == "#7B4F24"


# ===================================================================== 2. printing
def test_the_watermark_follows_the_real_status():
    assert print_settings.watermark_for("مسودة") == "مسودة" and print_settings.watermark_for("معتمد") == ""
    assert print_settings.watermark_for("ملغي") == "ملغي" and print_settings.watermark_for("ملغاة") == "ملغاة"
    assert print_settings.watermark_for("بانتظار قرار الإدارة") == ""                         # a working paper for the decision, not a draft
    k = print_settings.kit("voucher", "PR-0001", 100, state="ملغي")
    assert k["watermark"] == "ملغي" and k["cancel"]
    print_settings.save("voucher", dict(print_settings.DEFAULT, watermark=False))
    assert print_settings.kit("voucher", "PR-0001", 100, state="مسودة")["watermark"] == ""


def test_the_verification_code_depends_on_number_and_amount():
    c = print_settings.code("po", "PO-1", 23174.80)
    assert re.fullmatch(r"[0-9A-F]{6}", c) and c == print_settings.code("po", "po-1", "23174.8")
    assert c != print_settings.code("po", "PO-1", 23175.80) and c != print_settings.code("po", "PO-2", 23174.80) and c != print_settings.code("voucher", "PO-1", 23174.80)
    text = print_settings.qr_text("po", "PO-1", 23174.80, datetime(2026, 10, 20), "شركة عنوان الفولاذ")
    assert "PO-1" in text and "23,174.80" in text and c in text and text.isascii()           # Latin only: a coarse code a phone reads from paper
    svg = print_settings.qr_svg(text)
    assert svg.startswith("<svg") and 'shape-rendering="crispEdges"' in svg


def test_the_printed_qr_code_stays_coarse_enough_to_be_read_from_paper():
    import segno
    longest = print_settings.qr_text("supplier_statement", "ST-S12345-20261118", 98765432.10, datetime(2026, 11, 18))
    q = segno.make(longest, error="m", micro=False)
    assert q.version <= 7                                                    # at most 45 squares a side: about 0.6 mm each at 30 mm wide
    svg = print_settings.qr_svg(longest)
    n = q.symbol_size(border=4)[0]
    assert 'viewBox="0 0 %d %d"' % (n, n) in svg                             # the white margin of 4 squares is part of the drawing


def test_a_printed_order_carries_its_qr_and_the_code_verifies_it(client):
    _order("PO-2026-900", items=(("عمود", 10, 115.0),))
    print_settings.save("po", dict(print_settings.DEFAULT, show_cr=True, footer="تذييل تجريبي", paper="A5"))
    fake_db.execute("UPDATE [CompanyProfile] SET [CR] = '1010999' WHERE [ProfileID] = 1")
    html = client.get("/orders/PO-2026-900/print").get_data(as_text=True)
    code = re.search(r'data-code="([0-9A-F]{6})"', html).group(1)
    assert "<svg" in html and "تذييل تجريبي" in html and "1010999" in html and "size: A5" in html and "html { zoom: 0.72; }" in html
    assert "pk-wm" not in html.split("</style>")[-1]                                         # a saved order prints clean
    ok = client.post("/verify", data={"no": "PO-2026-900", "code": code}).get_data(as_text=True)
    assert "مستند صحيح" in ok and "1,322.50" in ok
    assert "لا يطابق" in client.post("/verify", data={"no": "PO-2026-900", "code": "ABCDEF"}).get_data(as_text=True)
    assert "لا يوجد مستند" in client.post("/verify", data={"no": "PO-NONE", "code": code}).get_data(as_text=True)


def test_every_setting_on_as_it_is_keeps_the_paper_unchanged_except_the_qr(client):
    _order("PO-2026-901")
    html = client.get("/orders/PO-2026-901/print").get_data(as_text=True)
    assert "@page { size" not in html.split("{% ")[0].split("@page { size: A4; margin: 9mm; }")[1] and "html { zoom" not in html
    print_settings.save("po", dict(print_settings.DEFAULT, qr=False))
    assert 'data-code="' not in client.get("/orders/PO-2026-901/print").get_data(as_text=True)


def test_a_draft_and_a_cancelled_voucher_carry_their_watermark_an_approved_one_does_not(client):
    from test_voucher_posting import _form
    s = fake_db.execute_insert("INSERT INTO [Suppliers] ([Name], [CreatedAt]) VALUES ('م', ?)", (datetime.now(),))
    client.post("/vouchers/new", data=_form("sup", s, 100, choice="no"))
    vid = fake_db.scalar("SELECT [VoucherID] FROM [PaymentVouchers] ORDER BY [VoucherID] DESC")
    html = client.get("/vouchers/%d/print" % vid).get_data(as_text=True)
    assert 'class="pk-wm"' in html and "<span>مسودة</span>" in html and 'data-code="' in html
    no = fake_db.scalar("SELECT [VoucherNo] FROM [PaymentVouchers] WHERE [VoucherID] = ?", (vid,))
    code = re.search(r'data-code="([0-9A-F]{6})"', html).group(1)
    assert "مستند صحيح" in client.post("/verify", data={"no": no, "code": code}).get_data(as_text=True)
    fake_db.execute("UPDATE [PaymentVouchers] SET [Status] = 'معتمد' WHERE [VoucherID] = ?", (vid,))
    assert 'class="pk-wm' not in client.get("/vouchers/%d/print" % vid).get_data(as_text=True)
    fake_db.execute("UPDATE [PaymentVouchers] SET [Status] = 'ملغي' WHERE [VoucherID] = ?", (vid,))
    html = client.get("/vouchers/%d/print" % vid).get_data(as_text=True)
    assert 'class="pk-wm cancel"' in html and html.count("vp-stamp\">ملغي") == 0                # one cancel mark, not two
    assert "vp-stamp" in client.get("/vouchers/%d" % vid).get_data(as_text=True)               # the screen still shows its own stamp


def test_statements_carry_a_code_checked_with_the_printed_balance(client):
    import helpers as h
    sub = h.add_sub("مقاول ق")
    html = client.get("/subcontractors/%d/statement/print" % sub).get_data(as_text=True)
    no = re.search(r'data-number="(ST-C%d-\d{8})"' % sub, html)
    code = re.search(r'data-code="([0-9A-F]{6})"', html).group(1)
    assert no and "مستند صحيح" in client.post("/verify", data={"no": no.group(1), "code": code, "amount": "0"}).get_data(as_text=True)
    assert "مستند صحيح" not in client.post("/verify", data={"no": no.group(1), "code": code, "amount": "5000"}).get_data(as_text=True)


def test_the_print_settings_page_saves_each_document_apart(client):
    assert client.get("/settings/print?doc=voucher").status_code == 200
    client.post("/settings/print", data={"doc": "voucher", "margin": "16mm", "font": "plex", "qr": "1", "watermark": "1", "footer": "  نص   "})
    assert print_settings.get("voucher")["margin"] == "16mm" and print_settings.get("voucher")["footer"] == "نص"
    assert print_settings.get("po") == print_settings.DEFAULT
    print_settings.reset_cache()
    assert print_settings.get("voucher")["font"] == "plex"
    assert "IBM Plex Sans Arabic" in print_settings.kit("voucher", "X", 1)["font_face"]


# ===================================================================== 3. supplier rating
def test_a_new_order_needs_its_agreed_delivery_date_and_an_edit_does_not(client):
    form = {"po_number": "PO-2026-950", "supplier": "مورد", "project": "م", "order_date": "2026-11-14", "discount": "0",
            "item_name": ["x"], "item_unit": ["u"], "item_qty": ["1"], "item_price": ["10"]}
    assert "تاريخ التوريد المتفق عليه مطلوب" in client.post("/orders/new", data=form).get_data(as_text=True)
    assert "قبل تاريخ الأمر" in client.post("/orders/new", data=dict(form, delivery_date="2026-11-01")).get_data(as_text=True)
    client.post("/orders/new", data=dict(form, delivery_date="2026-11-30"))
    o = fake_db.query("SELECT * FROM [PurchaseOrders] WHERE [PONumber] = 'PO-2026-950'")[0]
    assert o["AgreedDeliveryDate"] == datetime(2026, 11, 30)
    assert "تاريخ التوريد المطلوب" in client.get("/orders/PO-2026-950/print").get_data(as_text=True)


def test_the_receipt_quality_is_kept(client):
    _order("PO-Q")
    client.post("/orders/PO-Q/status", data={"kind": "receipt", "value": "مكتمل", "quality": "بملاحظات"})
    assert fake_db.scalar("SELECT [ReceiptQuality] FROM [PurchaseOrders] WHERE [PONumber] = 'PO-Q'") == "بملاحظات"
    client.post("/orders/PO-Q/status", data={"kind": "receipt", "value": "لم يبدأ", "quality": "سليم"})
    assert fake_db.scalar("SELECT [ReceiptQuality] FROM [PurchaseOrders] WHERE [PONumber] = 'PO-Q'") is None


def test_delivery_state_of_an_order():
    agreed = datetime(2026, 11, 10)
    today = datetime(2026, 11, 16)
    assert supplier_rating.delivery_state({"AgreedDeliveryDate": agreed, "ReceiptStatus": "لم يبدأ"}, today) == ("متأخر 6 يوماً", "danger")
    assert supplier_rating.delivery_state({"AgreedDeliveryDate": agreed, "ReceiptStatus": "مكتمل", "ReceiptDate": datetime(2026, 11, 9)}, today)[1] == "success"
    assert supplier_rating.delivery_state({"AgreedDeliveryDate": agreed, "ReceiptStatus": "مكتمل", "ReceiptDate": datetime(2026, 11, 12)}, today) == ("وصل متأخراً 2 يوماً", "warning")
    assert supplier_rating.delivery_state({"AgreedDeliveryDate": None}) is None


def test_the_score_comes_from_the_data_and_a_criterion_without_data_does_not_count():
    today = datetime(2026, 11, 16)
    # A: two orders on time, sold «حديد» at 100 (B at 150), received «سليم»
    _order("A1", "مورد أ", agreed=datetime(2026, 11, 1), receipt="مكتمل", got=datetime(2026, 10, 30), quality="سليم")
    _order("A2", "مورد أ", agreed=datetime(2026, 11, 5), receipt="مكتمل", got=datetime(2026, 11, 5), quality="سليم")
    # B: one late (not received, past the date), one received late, dearer, «بملاحظات»
    _order("B1", "مورد ب", agreed=datetime(2026, 11, 1), items=(("حديد", 1, 150.0),), quality="بملاحظات", receipt="مكتمل", got=datetime(2026, 11, 9))
    _order("B2", "مورد ب", agreed=datetime(2026, 11, 3), items=(("اسمنت", 1, 10.0),))
    # C: an old order with nothing new: only its papers count
    _order("C1", "مورد ج", items=(("رمل", 1, 5.0),))
    rows = {r["name"]: r for r in supplier_rating.compute(today)}
    a, b, c = rows["مورد أ"], rows["مورد ب"], rows["مورد ج"]
    assert a["parts"]["time"]["score"] == 100 and b["parts"]["time"]["score"] == 0 and "time" not in c["parts"]
    assert a["parts"]["price"]["score"] == 100 and b["parts"]["price"]["score"] == 60 and "أعلى من متوسط الموردين بـ 20%" == b["parts"]["price"]["text"]
    assert a["parts"]["quality"]["score"] == 100 and b["parts"]["quality"]["score"] == 60
    assert a["parts"]["docs"]["score"] == 0 and "ناقص" in a["parts"]["docs"]["text"]
    # A = (100*40 + 100*30 + 100*20 + 0*10) / 100 = 90 ;  C has docs only -> 0
    assert a["score"] == 90 and a["grade"] == "A" and c["score"] == 0 and c["grade"] == "D"
    assert [r["name"] for r in supplier_rating.compute(today)][0] == "مورد أ"


def test_a_foreign_currency_price_is_compared_in_riyals():
    _order("U1", "مورد دولي", items=(("لوح", 1, 100.0),), rate=3.75)       # 375 riyals
    _order("U2", "مورد محلي", items=(("لوح", 1, 375.0),))
    rows = {r["name"]: r for r in supplier_rating.compute()}
    assert rows["مورد دولي"]["parts"]["price"]["score"] == rows["مورد محلي"]["parts"]["price"]["score"] == 100


def test_papers_on_file_and_not_expired_count(client):
    sid = _order("P1", "مورد ورق")
    fake_db.execute("INSERT INTO [Documents] ([EntityType], [EntityKey], [DocType], [StoredName], [OriginalName], [RelPath], [UploadedAt], [IsCurrent], [ExpiryDate]) "
                    "VALUES ('SUPPLIER', ?, 'السجل التجاري', 'a', 'a', 'a', ?, True, ?)", (str(sid), datetime.now(), datetime.now() + timedelta(days=90)))
    fake_db.execute("INSERT INTO [Documents] ([EntityType], [EntityKey], [DocType], [StoredName], [OriginalName], [RelPath], [UploadedAt], [IsCurrent], [ExpiryDate]) "
                    "VALUES ('SUPPLIER', ?, 'الشهادة الضريبية', 'b', 'b', 'b', ?, True, ?)", (str(sid), datetime.now(), datetime.now() - timedelta(days=1)))
    p = supplier_rating.for_supplier(sid)["parts"]["docs"]
    assert p["score"] == 50 and "الشهادة الضريبية" in p["text"]


def test_the_weights_must_add_up_to_100_and_only_the_administrator_sets_them(client):
    assert supplier_rating.weights() == {"time": 40, "price": 30, "quality": 20, "docs": 10}
    assert supplier_rating.save_weights({"time": "50", "price": "30", "quality": "20", "docs": "10"})[1]
    client.post("/suppliers/rating/weights", data={"time": "50", "price": "20", "quality": "20", "docs": "10"})
    assert supplier_rating.weights()["time"] == 50
    _order("W1")
    assert client.get("/suppliers/rating").status_code == 200
    _uid, c = _user("procurement", "buyer2")
    assert c.get("/suppliers/rating").status_code == 200 and c.post("/suppliers/rating/weights", data={}).status_code == 403
    assert "حفظ الأوزان" not in c.get("/suppliers/rating").get_data(as_text=True)


# ===================================================================== 4. alerts
def test_alerts_reach_the_chosen_roles_and_people_only_and_respect_permissions(client):
    _order("PO-LATE", agreed=datetime.now() - timedelta(days=3))
    buyer, cb = _user("procurement", "buyer3")
    money, cm = _user("finance", "money1")
    assert "PO-LATE" in cb.get("/notifications").get_data(as_text=True)                      # «توريد متأخر» goes to purchasing
    assert "PO-LATE" not in cm.get("/notifications").get_data(as_text=True)                   # ... not to finance
    alerts.save_settings(_alert_form(late_delivery={"roles": [], "users": [money]}))
    assert "PO-LATE" in cm.get("/notifications").get_data(as_text=True) and "PO-LATE" not in cb.get("/notifications").get_data(as_text=True)
    viewer, cv = _user("viewer", "view1") if fake_db.scalar("SELECT [RoleID] FROM [Roles] WHERE [RoleKey] = 'viewer'") else (None, None)
    if cv:                                                                                    # named, but the page is not his: nothing shown
        alerts.save_settings(_alert_form(late_delivery={"roles": [], "users": [viewer]}))
        perms = {r["PermCode"] for r in fake_db.query("SELECT [PermCode] FROM [RolePermissions] rp INNER JOIN [Roles] r ON rp.[RoleID] = r.[RoleID] WHERE r.[RoleKey] = 'viewer'")}
        assert ("PO-LATE" in cv.get("/notifications").get_data(as_text=True)) == ("orders.view" in perms)


def _alert_form(**over):
    """A settings form = the current settings, with some rules changed."""
    from werkzeug.datastructures import MultiDict
    cfg = alerts.settings()
    for k, v in over.items():
        cfg[k].update(v)
    f = MultiDict()
    for k, c in cfg.items():
        if c["on"]:
            f.add("on_" + k, "1")
        if c["days"] is not None:
            f.add("days_" + k, str(c["days"]))
        for r in c["roles"]:
            f.add("roles_" + k, r)
        for u in c["users"]:
            f.add("users_" + k, str(u))
    return f


def test_the_bell_counts_and_marking_read_hides_an_alert_until_it_changes(client):
    _order("PO-LATE2", agreed=datetime.now() - timedelta(days=2))
    html = client.get("/orders").get_data(as_text=True)
    assert 'class="bell"' in html and "PO-LATE2" in html
    key = [a for a in alerts.for_user(_admin(), lambda c: True, lambda e, **k: "/x") if "PO-LATE2" in a["text"]][0]["key"]
    client.post("/notifications/read", data={"key": key})
    assert "PO-LATE2" not in client.get("/notifications").get_data(as_text=True)
    assert "PO-LATE2" in client.get("/notifications?all=1").get_data(as_text=True)
    client.post("/notifications/read", data={"all": "1"})
    assert "لا توجد تنبيهات جديدة" in client.get("/notifications").get_data(as_text=True)


def _admin():
    u = fake_db.query("SELECT * FROM [Users] WHERE [UserID] = 1")[0]
    u["RoleKey"] = "admin"
    return u


def test_each_rule_finds_its_cases():
    now = datetime.now()
    _order("PO-INV", InvoiceStatus="تم الإصدار", InvoiceDate=now - timedelta(days=40))
    _order("PO-INV-NEW", InvoiceStatus="تم الإصدار", InvoiceDate=now - timedelta(days=5))
    _order("PO-NOFILES", OrderDate=now - timedelta(days=10))
    cfg = alerts.settings()
    assert [a[0] for a in alerts._unpaid_invoice(cfg["unpaid_invoice"])] == ["unpaid:PO-INV"]
    assert "files:PO-NOFILES" in [a[0] for a in alerts._missing_files(cfg["missing_files"])]
    sid = fake_db.scalar("SELECT [SupplierID] FROM [Suppliers]")
    fake_db.execute("INSERT INTO [Documents] ([EntityType], [EntityKey], [DocType], [StoredName], [OriginalName], [RelPath], [UploadedAt], [IsCurrent], [ExpiryDate]) "
                    "VALUES ('SUPPLIER', ?, 'السجل التجاري', 'a', 'a', 'a', ?, True, ?)", (str(sid), now, now + timedelta(days=10)))
    found = alerts._expiring_docs(cfg["expiring_docs"])
    assert len(found) == 1 and "ينتهي بعد 10 يوماً" in found[0][1]
    import helpers as h
    sub = h.add_sub("مقاول ر")
    cid = fake_db.execute_insert("INSERT INTO [Contracts] ([SubcontractorID], [ContractNo], [Status], [RetentionDueDate]) VALUES (?, 'SC-9', 'ساري', ?)", (sub, now + timedelta(days=20)))
    assert [a[0] for a in alerts._retention_due(cfg["retention_due"])] == ["retention:%d:s" % cid]
    assert alerts.RULES["missing_files"][7] is False and not alerts.settings()["missing_files"]["on"]          # off unless switched on


def test_alert_settings_are_checked(client):
    assert client.get("/settings/alerts").status_code == 200
    _cfg, errors = alerts.save_settings(_alert_form(late_delivery={"roles": [], "users": []}))
    assert errors and "لم تختر له دوراً" in errors[0]
    f = _alert_form()
    f.setlist("days_unpaid_invoice", ["abc"])
    assert alerts.save_settings(f)[1]
    _uid, c = _user("procurement", "buyer4")
    assert c.get("/settings/alerts").status_code == 403


def test_a_broken_rule_never_breaks_a_page(client, monkeypatch):
    monkeypatch.setitem(alerts.CHECKS, "late_delivery", lambda cfg: 1 / 0)
    assert client.get("/orders").status_code == 200 and client.get("/notifications").status_code == 200


def test_the_contract_page_keeps_the_retention_due_date(client):
    import helpers as h
    sub = h.add_sub("مقاول ع")
    cid = fake_db.execute_insert("INSERT INTO [Contracts] ([SubcontractorID], [ContractNo], [Status]) VALUES (?, 'SC-1', 'ساري')", (sub,))
    client.post("/contracts/%d/edit" % cid, data={"contract_no": "SC-1", "status": "ساري", "contract_value": "1000", "retention_pct": "5", "advance_pct": "0", "retention_due": "2027-03-01"})
    assert fake_db.scalar("SELECT [RetentionDueDate] FROM [Contracts] WHERE [ContractID] = ?", (cid,)) == datetime(2027, 3, 1)
    client.post("/contracts/%d/edit" % cid, data={"contract_no": "SC-1", "status": "ساري", "contract_value": "1000", "retention_pct": "5", "advance_pct": "0"})
    assert fake_db.scalar("SELECT [RetentionDueDate] FROM [Contracts] WHERE [ContractID] = ?", (cid,)) == datetime(2027, 3, 1)      # a form without the field leaves it

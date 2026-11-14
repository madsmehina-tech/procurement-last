# -*- coding: utf-8 -*-
"""v28.0 - the payment voucher: a number that can be typed, other currencies, a preview that saves nothing, the signed scan, and deleting for good."""
import io
from datetime import datetime

import pytest

import archive
import conftest
import fake_db
import helpers as h
import signatures
import voucher_service as vs
from finance import ST_ENTERED, ST_PAID
from test_voucher_posting import PROJECT, _form, _vid, _voucher

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


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


def _sup(name="مورد الاختبار"):
    return fake_db.execute_insert("INSERT INTO [Suppliers] ([Name], [CreatedAt]) VALUES (?, ?)", (name, datetime.now()))


def _numbers():
    return [r["VoucherNo"] for r in fake_db.query("SELECT [VoucherNo] FROM [PaymentVouchers] ORDER BY [VoucherID]")]


# ------------------------------------------------------------------ the number
def test_the_automatic_number_is_the_last_in_the_list_plus_one_and_a_typed_number_is_kept(client):
    s = _sup()
    client.post("/vouchers/new", data=_form("sup", s, 100, choice="no"))
    client.post("/vouchers/new", data=_form("sup", s, 100, choice="no", voucher_no="PR-0050"))
    client.post("/vouchers/new", data=_form("sup", s, 100, choice="no"))
    assert _numbers() == ["PR-0001", "PR-0050", "PR-0051"] and vs.suggest_no() == "PR-0052"


def test_a_number_already_used_is_refused_and_nothing_is_saved(client):
    s = _sup()
    client.post("/vouchers/new", data=_form("sup", s, 100, choice="no"))
    page = client.post("/vouchers/new", data=_form("sup", s, 250, choice="no", voucher_no="pr-0001")).get_data(as_text=True)
    assert "مستعمل" in page and _numbers() == ["PR-0001"]
    assert 'value="250' in page                                                    # what was typed is still on the form
    assert "حروف إنجليزية" in client.post("/vouchers/new", data=_form("sup", s, 100, choice="no", voucher_no="رقم ١")).get_data(as_text=True)
    assert _numbers() == ["PR-0001"]


def test_deleting_the_last_voucher_gives_its_number_again_and_a_draft_can_be_renumbered(client):
    s = _sup()
    for _ in range(3):
        client.post("/vouchers/new", data=_form("sup", s, 100, choice="no"))
    vs.delete(_vid(), "admin", "أُدخل مرتين")
    assert _numbers() == ["PR-0001", "PR-0002"] and vs.suggest_no() == "PR-0003"
    client.post("/vouchers/%d/edit" % _vid(), data=_form("sup", s, 100, choice="no", voucher_no="PR-0009"))
    assert _numbers() == ["PR-0001", "PR-0009"]
    page = client.post("/vouchers/%d/edit" % _vid(), data=_form("sup", s, 100, choice="no", voucher_no="PR-0001")).get_data(as_text=True)
    assert "مستعمل" in page and _numbers() == ["PR-0001", "PR-0009"]


# ------------------------------------------------------------------ currency
def test_a_dollar_voucher_keeps_its_amount_prints_dollars_and_posts_riyals(client):
    s = _sup()
    client.post("/vouchers/new", data=_form("sup", s, 1000, currency="USD", exchange_rate="3.75"))
    v = _voucher(_vid())
    assert (v["CurrencyCode"], v["ExchangeRate"], v["Net"], v["NetSAR"]) == ("USD", 3.75, 1000.0, 3750.0)
    ctx = vs.print_context(v, {})
    assert "دولار" in ctx["words"] and "ريال" not in ctx["words"] and ctx["net_sar"] == 3750.0
    client.post("/vouchers/%d/approve" % v["VoucherID"])
    e = fake_db.query("SELECT * FROM [SupplierEntries] WHERE [SupplierID] = ?", (s,))
    assert len(e) == 1 and e[0]["Amount"] == 3750.0                                # the account is in riyals
    page = client.get("/vouchers").get_data(as_text=True)
    assert "3,750.00" in page and "1,000.00 USD" in page


def test_another_currency_needs_a_rate_and_riyals_ignore_it(client):
    s = _sup()
    page = client.post("/vouchers/new", data=_form("sup", s, 1000, currency="EUR", exchange_rate="")).get_data(as_text=True)
    assert "سعر الصرف" in page and _numbers() == []
    client.post("/vouchers/new", data=_form("sup", s, 1000, currency="SAR", exchange_rate="9"))
    v = _voucher(_vid())
    assert (v["CurrencyCode"], v["ExchangeRate"], v["NetSAR"]) == ("SAR", 1.0, 1000.0)
    client.post("/vouchers/new", data=_form("sup", s, 1000, currency="XXX"))          # an unknown code is treated as riyals
    assert _voucher(_vid())["CurrencyCode"] == "SAR"


# ------------------------------------------------------------------ preview
def test_preview_shows_the_printed_form_and_saves_nothing(client):
    s = _sup("مؤسسة الرؤية الحديثة")
    page = client.post("/vouchers/preview", data=_form("sup", s, 73493.62, choice="no", voucher_no="PR-0777")).get_data(as_text=True)
    assert "معاينة" in page and "PR-0777" in page and "مؤسسة الرؤية الحديثة" in page and "73,493.62" in page
    assert _numbers() == [] and fake_db.query("SELECT * FROM [AuditLog] WHERE [ActionName] = 'voucher_create'") == []
    bad = client.post("/vouchers/preview", data=_form("sup", s, 0, choice="no"))
    assert bad.status_code == 400


# ------------------------------------------------------------------ signed scan
def test_the_signed_scan_is_stored_next_to_the_voucher_and_replacing_keeps_the_old_one(client):
    s = _sup()
    client.post("/vouchers/new", data=_form("sup", s, 100, choice="no"))
    vid = _vid()
    r = client.post("/vouchers/%d/signed" % vid, data={"signed_file": (io.BytesIO(PDF), "scan.pdf")}, content_type="multipart/form-data", follow_redirects=True)
    assert "بعد اعتماد" in r.get_data(as_text=True) and vs.signed_copy(vid) is None      # a draft cannot take a signed copy
    client.post("/vouchers/%d/approve" % vid)
    client.post("/vouchers/%d/signed" % vid, data={"signed_file": (io.BytesIO(PDF), "scan.pdf")}, content_type="multipart/form-data")
    first = vs.signed_copy(vid)
    folder = archive.voucher_folder_rel("PR-0001", datetime(2026, 10, 24))
    assert first["RelPath"].startswith(folder + "/") and "PR-0001" in first["StoredName"]
    page = client.get("/vouchers").get_data(as_text=True)
    assert "فتح الموقّع" in page and "استبدال" in page
    client.post("/vouchers/%d/signed" % vid, data={"signed_file": (io.BytesIO(PDF + b"\n%2"), "scan2.pdf")}, content_type="multipart/form-data")
    docs = [d for d in archive.list_documents("VOUCHER", vid, current_only=False) if d["DocType"] == vs.SIGNED_DOC]
    assert len(docs) == 2 and sum(1 for d in docs if d["IsCurrent"]) == 1 and vs.signed_copy(vid)["DocID"] != first["DocID"]
    bad = client.post("/vouchers/%d/signed" % vid, data={"signed_file": (io.BytesIO(b"x"), "a.exe")}, content_type="multipart/form-data", follow_redirects=True)
    assert "PDF أو صورة" in bad.get_data(as_text=True)


# ------------------------------------------------------------------ delete for good
def test_delete_takes_the_supplier_payment_out_of_the_account_and_keeps_an_audit_line(client):
    s = _sup()
    client.post("/vouchers/new", data=_form("sup", s, 5000))
    vid = _vid()
    client.post("/vouchers/%d/approve" % vid)
    assert len(fake_db.query("SELECT * FROM [SupplierEntries]")) == 1
    with pytest.raises(vs.VoucherError):
        vs.delete(vid, "admin", "")                                                # a reason is required
    msg = vs.delete(vid, "admin", "أُدخل بالخطأ")
    assert "كشف حساب المورد" in msg and fake_db.query("SELECT * FROM [SupplierEntries]") == [] and fake_db.query("SELECT * FROM [PaymentVouchers]") == []
    log = fake_db.query("SELECT * FROM [AuditLog] WHERE [ActionName] = 'voucher_delete'")
    assert len(log) == 1 and "أُدخل بالخطأ" in log[0]["Details"] and "PR-0001" in str(log[0]["EntityKey"])


def test_delete_puts_a_paid_certificate_back_and_removes_a_direct_payment(client):
    sub = h.add_sub()
    c = h.add_cert(sub, 20000, no=7)
    client.post("/vouchers/new", data=_form("sub", sub, 20000, [("مستخلص", "7", str(c))]))
    vid = _vid()
    client.post("/vouchers/%d/approve" % vid)
    assert fake_db.query("SELECT [Status] FROM [Certificates] WHERE [CertificateID] = ?", (c,))[0]["Status"] == ST_PAID
    vs.delete(vid, "admin", "خطأ في المبلغ")
    g = fake_db.query("SELECT * FROM [Certificates] WHERE [CertificateID] = ?", (c,))[0]
    assert g["Status"] == ST_ENTERED and g["PaymentDate"] is None and fake_db.query("SELECT * FROM [Payments]") == []
    client.post("/vouchers/new", data=_form("sub", sub, 333))                        # no certificate named: a direct payment in the statement
    vid = _vid()
    client.post("/vouchers/%d/approve" % vid)
    assert len([e for e in fake_db.query("SELECT * FROM [LedgerEntries]") if e.get("BankRef") == _voucher(vid)["VoucherNo"]]) == 1
    no = _voucher(vid)["VoucherNo"]
    vs.delete(vid, "admin", "خطأ")
    assert [e for e in fake_db.query("SELECT * FROM [LedgerEntries]") if e.get("BankRef") == no] == []


def test_only_the_administrator_deletes_and_the_files_go_to_the_trash(client):
    s = _sup()
    client.post("/vouchers/new", data=_form("sup", s, 100, choice="no"))
    vid = _vid()
    client.post("/vouchers/%d/approve" % vid)
    client.post("/vouchers/%d/signed" % vid, data={"signed_file": (io.BytesIO(PDF), "scan.pdf")}, content_type="multipart/form-data")
    path = archive.abs_path(vs.signed_copy(vid)["RelPath"])
    import os
    assert os.path.isfile(path)
    viewer = [r["RoleID"] for r in fake_db.query("SELECT * FROM [Roles]") if r["RoleKey"] != "admin"][0]
    uid = fake_db.execute_insert("INSERT INTO [Users] ([Username], [PasswordHash], [FullName], [Status], [RoleID], [Role], [CreatedAt]) VALUES ('u2', 'x', 'u2', 'active', ?, 'user', ?)", (viewer, datetime.now()))
    import app as appmod
    c2 = appmod.app.test_client()
    with c2.session_transaction() as sess:
        sess["user_id"] = uid
    c2.post("/vouchers/%d/delete" % vid, data={"reason": "أريد حذفه"})
    assert len(fake_db.query("SELECT * FROM [PaymentVouchers]")) == 1               # refused
    r = client.post("/vouchers/%d/delete" % vid, data={"reason": "أُدخل مرتين"}, follow_redirects=True)
    assert "حُذف أمر الصرف" in r.get_data(as_text=True) and fake_db.query("SELECT * FROM [PaymentVouchers]") == []
    assert not os.path.exists(path) and fake_db.query("SELECT * FROM [Documents] WHERE [EntityType] = 'VOUCHER'") == []


# ------------------------------------------------------------------ signatures
def test_signature_boxes_can_be_added_and_the_added_ones_print_on_the_row_above(client):
    items = [{"title": "t%d" % i, "name": ""} for i in range(1, 8)]
    rows = signatures.rows(items, 5)
    assert [len(r) for r in rows] == [2, 5] and rows[0][0]["title"] == "t1"         # the first (added on top) go on a short row above a full row
    assert signatures.rows(items[:5], 5) == [items[:5]] and signatures.rows([], 5) == []
    s = _sup()
    f = _form("sup", s, 100, choice="no")
    f.update({"voucher_sign_title": ["مراجع داخلي", "مدير الحسابات", ""], "voucher_sign_name": ["سارة", "", "بلا مسمّى"]})
    page = client.post("/vouchers/new", data=f).get_data(as_text=True)
    assert "اكتب المسمّى" in page
    f["voucher_sign_name"] = ["سارة", "", ""]
    client.post("/vouchers/new", data=f)
    ctx = vs.print_context(_voucher(_vid()), {})
    assert [(x["title"], x["name"]) for x in ctx["signs"]] == [("مراجع داخلي", "سارة"), ("مدير الحسابات", "")]
    old = dict(_voucher(_vid()), SignJson='{"contracts": "أحمد", "gm": "خالد"}')       # a voucher saved before 28.0 still prints its five boxes
    signs = vs.print_context(old, {})["signs"]
    assert len(signs) == 5 and signs[0]["name"] == "أحمد" and signs[4]["name"] == "خالد"

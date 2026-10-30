# -*- coding: utf-8 -*-
"""«هل تريد إضافة الدفعة إلى كشف الحساب؟» - the voucher can record the payment as «تم التحويل» when it is APPROVED, once, atomically."""
from datetime import datetime

import pytest

import conftest
import contracts_service as cs
import fake_db
import helpers as h
import ledger_service as ls
import opening_service as op
import supplier_account as sa
import voucher_service as vs

PROJECT = "مشروع 1"                                  # the project helpers.add_cert uses


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


def _form(kind, party_id, amount, refs=(), choice="yes", **over):
    f = {"voucher_date": "2026-10-24", "project_id": str(h.project_id(PROJECT)), "party_kind": kind, "party_id": str(party_id), "request_type": "أعمال", "pay_method": "تحويل بنكي",
         "description": "سداد", "amount_mode": "incl", "vat_pct": "15", "item_desc": ["سداد"], "item_qty": ["1"], "item_price": [str(amount)],
         "ref_type": [r[0] for r in refs], "ref_no": [r[1] for r in refs], "ref_id": [r[2] for r in refs], "post_choice": choice}
    f.update(over)
    return f


def _vid():
    return fake_db.scalar("SELECT [VoucherID] FROM [PaymentVouchers] ORDER BY [VoucherID] DESC")


def _approve(client, vid):
    return client.post("/vouchers/%d/approve" % vid, follow_redirects=True).get_data(as_text=True)


def _voucher(vid):
    return fake_db.query("SELECT * FROM [PaymentVouchers] WHERE [VoucherID] = ?", (vid,))[0]


def test_a_certificate_that_matches_the_amount_is_paid_in_full_exactly_like_the_pay_button(client):
    s = h.add_sub()
    c = h.add_cert(s, 144293.39, no=2)
    client.post("/vouchers/new", data=_form("sub", s, 144293.39, [("مستخلص", "2", str(c))]))
    vid = _vid()
    assert fake_db.scalar("SELECT COUNT(*) FROM [Payments]") == 0                      # nothing is posted before the approval
    text = _approve(client, vid)
    v = _voucher(vid)
    assert v["Status"] == "معتمد" and v["PostedKind"] == "cert" and v["PostedAt"] and "تم التحويل" in text
    assert h.paid(c) == 144293.39 and h.cert_row(c)["Status"] == "مصروف"
    assert fake_db.query("SELECT [BankRef] FROM [Payments]")[0]["BankRef"] == v["VoucherNo"]
    assert ls.statement(s)["balance"] == 0                                              # the contractor's statement shows the payment
    _subs, contracts, certs, _p = cs.load()
    assert cs.sub_figures(s, contracts, certs)["remaining"] == 0


def test_approving_twice_never_pays_twice(client):
    s = h.add_sub()
    c = h.add_cert(s, 1000, no=1)
    client.post("/vouchers/new", data=_form("sub", s, 1000, [("مستخلص", "1", str(c))]))
    vid = _vid()
    _approve(client, vid)
    again = _approve(client, vid)
    assert "مسودة" in again and fake_db.scalar("SELECT COUNT(*) FROM [Payments]") == 1


def test_an_amount_that_does_not_match_becomes_a_direct_payment_and_leaves_the_certificate_alone(client):
    s = h.add_sub()
    c = h.add_cert(s, 1000, no=1)
    client.post("/vouchers/new", data=_form("sub", s, 400, [("مستخلص", "1", str(c))]))
    vid = _vid()
    text = _approve(client, vid)
    v = _voucher(vid)
    assert v["PostedKind"] == "ledger" and "دفعة مباشرة" in text and "لم تتغير حالة أي مستخلص" in text
    e = fake_db.query("SELECT * FROM [LedgerEntries] WHERE [Category] = 'دفعة مباشرة'")[0]
    assert e["Kind"] == "دائن" and e["Amount"] == 400 and e["Status"] == "منفّذة" and e["BankRef"] == v["VoucherNo"] and e["PayNo"] == 1
    assert h.cert_row(c)["Status"] == "مُدخَل" and ls.statement(s)["balance"] == -600


def test_without_a_referenced_certificate_it_is_a_direct_payment(client):
    s = h.add_sub()
    client.post("/vouchers/new", data=_form("sub", s, 250))
    _approve(client, _vid())
    assert fake_db.scalar("SELECT [Amount] FROM [LedgerEntries] WHERE [Category] = 'دفعة مباشرة'") == 250


def test_the_answer_no_posts_nothing(client):
    s = h.add_sub()
    c = h.add_cert(s, 1000, no=1)
    client.post("/vouchers/new", data=_form("sub", s, 1000, [("مستخلص", "1", str(c))], choice="no"))
    _approve(client, _vid())
    assert _voucher(_vid())["Status"] == "معتمد" and not _voucher(_vid())["PostedAt"]
    assert fake_db.scalar("SELECT COUNT(*) FROM [Payments]") == 0 and fake_db.scalar("SELECT COUNT(*) FROM [LedgerEntries]", default=0) == 0


def test_a_certificate_inside_a_payment_cycle_blocks_the_approval_and_nothing_is_saved(client):
    s = h.add_sub()
    c = h.add_cert(s, 1000, no=1, status="ضمن دورة صرف", CycleID=1)
    client.post("/vouchers/new", data=_form("sub", s, 1000, [("مستخلص", "1", str(c))]))
    vid = _vid()
    text = _approve(client, vid)
    assert "ضمن دورة صرف" in text and _voucher(vid)["Status"] == "مسودة"
    assert fake_db.scalar("SELECT COUNT(*) FROM [Payments]") == 0


def test_if_the_payment_cannot_be_saved_the_approval_is_undone_too(client, monkeypatch):
    s = h.add_sub()
    client.post("/vouchers/new", data=_form("sub", s, 250))
    vid = _vid()

    def boom(*a, **k):
        raise RuntimeError("database refused")
    monkeypatch.setattr(ls, "add_entry", boom)
    client.post("/vouchers/%d/approve" % vid)
    assert _voucher(vid)["Status"] == "مسودة" and not _voucher(vid)["PostedAt"]


def test_the_opening_date_guard_applies_to_the_payment(client):
    s = h.add_sub()
    op.set_sub_opening(s, op.DIR_OWE_PARTY, 5000, datetime(2026, 11, 1), "", "t")
    client.post("/vouchers/new", data=_form("sub", s, 250))                              # dated 2026-10-24: before the opening date
    vid = _vid()
    text = _approve(client, vid)
    assert "يسبق تاريخ رصيد الافتتاح" in text and _voucher(vid)["Status"] == "مسودة"


def test_a_supplier_payment_is_added_to_his_statement_and_the_order_is_not_counted_twice(client):
    sid = fake_db.execute_insert("INSERT INTO [Suppliers] ([Name], [CreatedAt]) VALUES ('مورد', ?)", (datetime.now(),))
    fake_db.execute("INSERT INTO [PurchaseOrders] ([PONumber], [SupplierID], [SupplierName], [Project], [TotalAmount], [OrderDate], [PaymentStatus], [CreatedAt]) "
                    "VALUES ('PO-5', ?, 'مورد', ?, 11500, ?, 'تم السداد', ?)", (sid, PROJECT, datetime(2026, 10, 1), datetime.now()))
    client.post("/vouchers/new", data=_form("sup", sid, 11500, [("أمر شراء", "PO-5", "PO-5")]))
    vid = _vid()
    _approve(client, vid)
    e = fake_db.query("SELECT * FROM [SupplierEntries]")[0]
    assert e["Kind"] == "دائن" and e["Amount"] == 11500 and e["PONumber"] == "PO-5" and e["BankRef"] == _voucher(vid)["VoucherNo"]
    st = sa.statement(sid)
    assert st["paid"] == 11500 and st["balance"] == 0                                    # the order marked «تم السداد» is not credited a second time


def test_a_posted_voucher_cannot_be_cancelled_until_the_payment_is_removed(client):
    s = h.add_sub()
    client.post("/vouchers/new", data=_form("sub", s, 250))
    vid = _vid()
    _approve(client, vid)
    text = client.post("/vouchers/%d/cancel" % vid, follow_redirects=True).get_data(as_text=True)
    assert "أزل الدفعة من الكشف أولاً" in text and _voucher(vid)["Status"] == "معتمد"
    ls.delete_entry(fake_db.scalar("SELECT [EntryID] FROM [LedgerEntries] WHERE [Category] = 'دفعة مباشرة'"))
    fake_db.execute("UPDATE [PaymentVouchers] SET [PostedAt] = NULL WHERE [VoucherID] = ?", (vid,))      # what the screens will do when the payment is removed
    client.post("/vouchers/%d/cancel" % vid)
    assert _voucher(vid)["Status"] == "ملغي"


def test_save_and_approve_in_one_click(client):
    s = h.add_sub()
    r = client.post("/vouchers/new", data=dict(_form("sub", s, 300), action="approve"), follow_redirects=True)
    v = _voucher(_vid())
    assert v["Status"] == "معتمد" and v["PostedKind"] == "ledger" and "اعتُمد" in r.get_data(as_text=True)


def test_the_form_shows_the_question_for_the_chosen_party_kind(client):
    html = client.get("/vouchers/new").get_data(as_text=True)
    assert "هل تريد إضافة هذه الدفعة إلى كشف حساب" in html and 'name="post_choice"' in html and "حفظ واعتماد" in html


def test_the_voucher_page_explains_what_will_happen_and_what_happened(client):
    s = h.add_sub()
    client.post("/vouchers/new", data=_form("sub", s, 250))
    vid = _vid()
    before = client.get("/vouchers/%d" % vid).get_data(as_text=True)
    assert "ستُسجَّل «تم التحويل» عند" in before and "ستُسجَّل الدفعة في كشف الحساب" in before
    _approve(client, vid)
    after = client.get("/vouchers/%d" % vid).get_data(as_text=True)
    assert "أُضيفت هذه الدفعة إلى كشف حساب المقاول" in after and "افتح كشف الحساب" in after


def test_the_company_page_has_the_current_signatories_and_saves_them_into_new_vouchers(client):
    html = client.get("/settings/company").get_data(as_text=True)
    for lab in ("مدير العقود والمشتريات", "مدير عام المشاريع", "مدير الحسابات", "المدير المالي", "المدير العام", "القسم (يظهر تحت اسم الشركة"):
        assert lab in html, lab
    assert "المحاسب" not in html
    data = {"legalname": "شركة الهضب", "signcontracts": "م أول", "signprojects": "م ثاني", "signaccounts": "ثالث", "signfinance": "رابع", "signgm": "خامس", "voucherdept": "قسم تجريبي"}
    client.post("/settings/company", data=data)
    row = fake_db.query("SELECT * FROM [CompanyProfile] WHERE [ProfileID] = 1")[0]
    assert (row["SignContracts"], row["SignProjects"], row["SignGM"], row["VoucherDept"]) == ("م أول", "م ثاني", "خامس", "قسم تجريبي")
    form = client.get("/vouchers/new").get_data(as_text=True)
    assert 'value="م أول"' in form and 'value="خامس"' in form and 'value="قسم تجريبي"' in form        # a new voucher starts with them


def test_approval_saves_a_pdf_copy_in_the_archive_and_the_voucher_page_links_to_it(client, monkeypatch):
    import cycle_files
    monkeypatch.setattr(cycle_files, "_pdf_bytes", lambda html: b"%PDF-1.4 fake " + str(len(html)).encode())
    s = h.add_sub()
    client.post("/vouchers/new", data=_form("sub", s, 250, choice="no"))
    vid = _vid()
    text = _approve(client, vid)
    assert "حُفظت نسخة PDF في الأرشيف" in text
    doc = fake_db.query("SELECT * FROM [Documents] WHERE [EntityType] = 'VOUCHER'")[0]
    assert doc["RelPath"].startswith("أوامر الصرف العامة/2026/") and doc["StoredName"].endswith(".pdf") and doc["IsCurrent"]
    page = client.get("/vouchers/%d" % vid).get_data(as_text=True)
    assert "نسخة الأرشيف" in page and doc["StoredName"] in page
    assert client.get("/documents/%d" % doc["DocID"]).status_code == 200                    # the link works
    client.post("/vouchers/%d/archive" % vid)                                           # again: replaces the same file, no second record
    assert fake_db.scalar("SELECT COUNT(*) FROM [Documents] WHERE [EntityType] = 'VOUCHER'") == 1


def test_a_pdf_failure_never_breaks_the_approval(client, monkeypatch):
    import cycle_files
    monkeypatch.setattr(cycle_files, "_pdf_bytes", lambda html: (_ for _ in ()).throw(RuntimeError("no browser")))
    s = h.add_sub()
    client.post("/vouchers/new", data=_form("sub", s, 250))
    text = _approve(client, _vid())
    assert _voucher(_vid())["Status"] == "معتمد" and "تعذّر حفظ نسخة PDF" in text and fake_db.scalar("SELECT COUNT(*) FROM [Payments] WHERE 1=1", default=0) == 0
    assert fake_db.scalar("SELECT COUNT(*) FROM [LedgerEntries] WHERE [Category] = 'دفعة مباشرة'") == 1        # the payment (answer «نعم») was still recorded

# -*- coding: utf-8 -*-
"""Opening balances (contractors and suppliers) and the supplier statement of account."""
from datetime import datetime

import pytest

import conftest
import contracts_service as cs
import fake_db
import helpers as h
import ledger_service as ls
import opening_service as op
import supplier_account as sa


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


D1 = datetime(2026, 11, 1)


def _supplier(name="مورد الحديد"):
    return fake_db.execute_insert("INSERT INTO [Suppliers] ([Name], [CreatedAt]) VALUES (?, ?)", (name, datetime.now()))


def _po(sid, name, number, total, order_date, project="برج 1", paid_on=None, invoiced=False):
    fake_db.execute("INSERT INTO [PurchaseOrders] ([PONumber], [SupplierID], [SupplierName], [Project], [TotalAmount], [OrderDate], [InvoiceStatus], [InvoiceDate], [PaymentStatus], [PaymentDate], [CreatedAt]) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (number, sid, name, project, total, order_date, "تم الإصدار" if invoiced else "معلق", order_date if invoiced else None,
                                                              "تم السداد" if paid_on else "معلق", paid_on, order_date))


# ------------------------------------------------------------------------------------------------ contractors
def test_a_contractor_opening_balance_is_the_first_line_and_raises_what_he_is_due():
    s = h.add_sub()
    op.set_sub_opening(s, op.DIR_OWE_PARTY, 500000, D1, "من النظام القديم", "t")
    st = ls.statement(s)
    assert st["lines"][0]["debit"] == 500000 and "رصيد افتتاحي" in st["lines"][0]["desc"]
    assert st["balance"] == -500000                                              # negative = the contractor still has dues from us


def test_the_contractor_figures_and_the_statement_agree_with_an_opening_balance():
    s = h.add_sub()
    op.set_sub_opening(s, op.DIR_OWE_PARTY, 100000, D1, "", "t")
    c = h.add_cert(s, 40000, CertificateDate=datetime(2026, 11, 5))
    fake_db.execute_insert("INSERT INTO [Payments] ([CertificateID], [Amount], [PaidAt]) VALUES (?, ?, ?)", (c, 10000, datetime(2026, 11, 9)))
    _subs, contracts, certs, _p = cs.load()
    fig = cs.sub_figures(s, contracts, certs)
    assert fig["remaining"] == pytest.approx(-ls.statement(s)["balance"]) == pytest.approx(130000)


def test_the_opposite_direction_lowers_what_is_due_and_one_row_only_exists():
    s = h.add_sub()
    op.set_sub_opening(s, op.DIR_PARTY_OWES, 25000, D1, "", "t")
    op.set_sub_opening(s, op.DIR_PARTY_OWES, 30000, D1, "", "t")                   # the second call replaces the first
    assert fake_db.scalar("SELECT COUNT(*) FROM [LedgerEntries] WHERE [Category] = 'رصيد افتتاحي'") == 1
    assert ls.statement(s)["balance"] == 30000                                    # positive: he owes us
    op.clear_sub_opening(s)
    assert ls.statement(s)["balance"] == 0 and op.sub_opening(s) is None


def test_things_dated_before_the_opening_date_are_refused_so_nothing_is_counted_twice():
    s = h.add_sub()
    op.set_sub_opening(s, op.DIR_OWE_PARTY, 1000, D1, "", "t")
    with pytest.raises(ls.LedgerError):
        ls.add_entry(s, "دائن", "دفعة مباشرة", "دفعة قديمة", 100, datetime(2026, 10, 20), "", "", True, "", "t")
    ls.add_entry(s, "دائن", "دفعة مباشرة", "دفعة جديدة", 100, datetime(2026, 11, 2), "", "", True, "", "t")


def test_a_certificate_dated_before_the_opening_date_is_refused_by_the_form(client):
    s = h.add_sub()
    op.set_sub_opening(s, op.DIR_OWE_PARTY, 1000, D1, "", "t")
    r = client.post("/subcontractors/%d/certificates" % s, data={"project": "م", "site": "ص", "cert_no": "1", "cert_date": "2026-10-15", "total_incl_vat": "5000"}, follow_redirects=True)
    assert "يسبق تاريخ رصيد الافتتاح" in r.get_data(as_text=True)
    assert fake_db.scalar("SELECT COUNT(*) FROM [Certificates]") == 0


def test_the_opening_line_cannot_be_deleted_from_the_movements_list():
    s = h.add_sub()
    op.set_sub_opening(s, op.DIR_OWE_PARTY, 1000, D1, "", "t")
    eid = fake_db.scalar("SELECT [EntryID] FROM [LedgerEntries] WHERE [Category] = 'رصيد افتتاحي'")
    with pytest.raises(ls.LedgerError):
        ls.delete_entry(eid)


def test_the_opening_form_on_the_contractor_page_saves_it(client):
    s = h.add_sub()
    r = client.post("/subcontractors/%d/opening" % s, data={"direction": "party", "amount": "1,250.50", "as_of": "2026-11-01", "note": "x"}, follow_redirects=True)
    assert "حُفظ رصيد الافتتاح" in r.get_data(as_text=True)
    assert op.sub_opening(s)["amount"] == 1250.5
    bad = client.post("/subcontractors/%d/opening" % s, data={"direction": "party", "amount": "abc", "as_of": "2026-11-01"}, follow_redirects=True)
    assert "غير صالح" in bad.get_data(as_text=True) and op.sub_opening(s)["amount"] == 1250.5


def test_the_new_contractor_form_can_carry_an_opening_balance(client):
    r = client.post("/subcontractors/new", data={"name": "مقاول جديد", "opening_amount": "77000", "opening_direction": "party", "opening_as_of": "2026-11-01"}, follow_redirects=True)
    sid = fake_db.scalar("SELECT [SubcontractorID] FROM [Subcontractors] WHERE [Name] = 'مقاول جديد'")
    assert op.sub_opening(sid)["amount"] == 77000 and "وسُجّل رصيد الافتتاح" in r.get_data(as_text=True)


# ------------------------------------------------------------------------------------------------ suppliers
def test_supplier_statement_purchases_payments_opening_and_balance():
    sid = _supplier()
    op.set_supplier_opening(sid, op.DIR_OWE_PARTY, 10000, D1, "", "t")
    _po(sid, "مورد الحديد", "PO-1", 5000, datetime(2026, 11, 3))
    _po(sid, "مورد الحديد", "PO-2", 3000, datetime(2026, 11, 4), paid_on=datetime(2026, 11, 10))       # paid in full
    sa.add_entry(sid, "دائن", "دفعة", "دفعة على الحساب", 2000, datetime(2026, 11, 12), "", "", "", "t")
    st = sa.statement(sid)
    assert st["purchases"] == 8000 and st["paid"] == 5000                           # 3000 (PO-2 paid) + 2000 typed
    assert st["balance"] == -(10000 + 8000 - 5000)                                  # he is still owed 13,000
    assert st["lines"][0]["kind"] == "opening"


def test_a_typed_payment_that_names_the_order_replaces_the_automatic_full_payment():
    sid = _supplier()
    _po(sid, "مورد الحديد", "PO-9", 3000, datetime(2026, 11, 4), paid_on=datetime(2026, 11, 10))
    sa.add_entry(sid, "دائن", "دفعة", "دفعة جزئية", 1000, datetime(2026, 11, 11), "", "PO-9", "", "t")
    st = sa.statement(sid)
    assert st["paid"] == 1000 and st["balance"] == -2000                            # not 3000 + 1000


def test_orders_before_the_opening_date_are_inside_the_opening_balance_but_later_payments_count():
    sid = _supplier()
    op.set_supplier_opening(sid, op.DIR_OWE_PARTY, 9000, D1, "", "t")
    _po(sid, "مورد الحديد", "PO-OLD", 9000, datetime(2026, 10, 1), paid_on=datetime(2026, 11, 20))   # ordered before, paid after
    st = sa.statement(sid)
    assert st["purchases"] == 0 and st["paid"] == 9000 and st["balance"] == 0


def test_filters_by_project_period_and_the_invoiced_basis():
    sid = _supplier()
    _po(sid, "مورد الحديد", "PO-A", 1000, datetime(2026, 11, 3), project="برج 1", invoiced=True)
    _po(sid, "مورد الحديد", "PO-B", 2000, datetime(2026, 11, 20), project="برج 2")
    assert sa.statement(sid, project="برج 2")["debit"] == 2000
    assert sa.statement(sid, date_to=datetime(2026, 11, 10))["debit"] == 1000
    prev = sa.statement(sid, date_from=datetime(2026, 11, 10))
    assert prev["opening"] == -1000 and prev["balance"] == -3000                   # the earlier movements become «رصيد سابق»
    assert sa.statement(sid, basis="invoiced")["debit"] == 1000                    # only the invoiced order


def test_supplier_entry_rules():
    sid = _supplier()
    with pytest.raises(sa.SupplierAccountError):
        sa.add_entry(sid, "دائن", "دفعة", "x", 0, None, "", "", "", "t")
    with pytest.raises(sa.SupplierAccountError):
        sa.add_entry(sid, "دائن", "دفعة", "x", 10, None, "", "PO-NOPE", "", "t")
    op.set_supplier_opening(sid, op.DIR_OWE_PARTY, 100, D1, "", "t")
    with pytest.raises(sa.SupplierAccountError):
        sa.add_entry(sid, "دائن", "دفعة", "قديمة", 10, datetime(2026, 10, 1), "", "", "", "t")
    eid = fake_db.scalar("SELECT [EntryID] FROM [SupplierEntries] WHERE [Category] = 'رصيد افتتاحي'")
    with pytest.raises(sa.SupplierAccountError):
        sa.delete_entry(eid)


def test_the_supplier_pages_render(client):
    sid = _supplier()
    op.set_supplier_opening(sid, op.DIR_OWE_PARTY, 10000, D1, "", "t")
    _po(sid, "مورد الحديد", "PO-1", 5000, datetime(2026, 11, 3))
    for url in ("/suppliers/%d/statement" % sid, "/suppliers/%d/statement/print" % sid, "/suppliers/%d#tab-account" % sid):
        r = client.get(url)
        assert r.status_code == 200, url
    html = client.get("/suppliers/%d/statement?project=برج 1&basis=invoiced" % sid).get_data(as_text=True)
    assert "كشف حساب المورد" in html
    assert "رصيد الافتتاح" in client.get("/suppliers/%d" % sid).get_data(as_text=True)


def test_adding_and_deleting_a_supplier_movement_through_the_pages(client):
    sid = _supplier()
    client.post("/suppliers/%d/entries/new" % sid, data={"kind": "دائن", "category": "دفعة", "description": "دفعة أولى", "amount": "1500", "entry_date": "2026-11-05"})
    assert sa.statement(sid)["paid"] == 1500
    eid = fake_db.scalar("SELECT [EntryID] FROM [SupplierEntries]")
    client.post("/suppliers/entries/%d/delete" % eid)
    assert sa.statement(sid)["paid"] == 0


def test_the_bulk_opening_page_saves_many_parties_at_once(client):
    s, sid = h.add_sub("مقاول أ"), _supplier("مورد ب")
    assert client.get("/settings/opening-balances").status_code == 200
    client.post("/settings/opening-balances", data={"as_of": "2026-11-01", "party": ["sub:%d" % s, "sup:%d" % sid], "amount_sub:%d" % s: "1000", "dir_sub:%d" % s: "party",
                                                    "amount_sup:%d" % sid: "250", "dir_sup:%d" % sid: "us"})
    assert op.sub_opening(s)["amount"] == 1000 and op.supplier_opening(sid)["direction"] == "us"


def test_the_migration_is_safe_to_run_twice():
    from migrations import m030_opening_vouchers
    m030_opening_vouchers.main()
    m030_opening_vouchers.main()
    assert fake_db.scalar("SELECT COUNT(*) FROM [PaymentVouchers]", default=0) == 0

# -*- coding: utf-8 -*-
"""
The money rules. Each test is a story from the audit report (AUDIT_REPORTS.md, C-01 ... C-05):
the contractor must never be paid more than the certificate, a certificate that has money on it must not be edited, and a
closed transaction must not move money.
"""
from datetime import datetime

import pytest

import conftest
import fake_db
import helpers as h


@pytest.fixture()
def world():
    conftest.build_schema()
    return h.add_sub()


@pytest.fixture()
def client(world):
    import app as appmod
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    return c


def _pay_form(client, cert_id):
    return client.post("/certificates/%d/pay" % cert_id, data={"pay_date": datetime.now().strftime("%Y-%m-%d"), "bank_ref": "X"})


# ------------------------------------------------------------------ C-01 double payment
def test_no_double_payment_when_a_released_line_is_paid_directly_then_executed(world):
    """Two certificates in one cycle. Line A is set «not executed» (it is released), A is paid directly, then A is flipped to «executed»."""
    import cycles_service as cy
    a, b = h.add_cert(world, 100000, no=1), h.add_cert(world, 50000, no=2)
    cid = h.cycle_to_execution({a: (cy.D_FULL, None), b: (cy.D_FULL, None)})
    cy.set_execution(cid, a, cy.EX_NOT, None, "", "", "tester")                      # released: status «مُدخَل», still inside the open cycle
    fake_db.execute("INSERT INTO [Payments] ([CertificateID], [SubcontractorID], [PaidAt], [Amount], [CycleID], [CreatedAt]) VALUES (?, ?, ?, ?, NULL, ?)",
                    (a, world, datetime.now(), 100000, datetime.now()))             # what «تسجيل صرف» does
    fake_db.execute("UPDATE [Certificates] SET [Status] = 'مصروف' WHERE [CertificateID] = ?", (a,))
    with pytest.raises(cy.CycleError):
        cy.set_execution(cid, a, cy.EX_DONE, datetime.now(), "REF", "", "tester")
    assert h.paid(a) == 100000.0


def test_no_double_payment_through_reopen_and_clear(world):
    """NOT executed -> paid directly -> cycle reopened -> execution cleared -> executed: the contractor must not get 200,000."""
    import cycles_service as cy
    a = h.add_cert(world, 100000, no=1)
    cid = h.cycle_to_execution({a: (cy.D_FULL, None)})
    cy.set_group_execution(cid, world, cy.EX_NOT, None, "", "", "tester")
    fake_db.execute("INSERT INTO [Payments] ([CertificateID], [SubcontractorID], [PaidAt], [Amount], [CycleID], [CreatedAt]) VALUES (?, ?, ?, ?, NULL, ?)",
                    (a, world, datetime.now(), 100000, datetime.now()))
    fake_db.execute("UPDATE [Certificates] SET [Status] = 'مصروف' WHERE [CertificateID] = ?", (a,))
    try:
        cy.go_back(cid, "tester")
        cy.clear_group_execution(cid, world, "tester")
        cy.set_group_execution(cid, world, cy.EX_DONE, datetime.now(), "REF", "", "tester")
    except cy.CycleError:
        pass                                                                       # refusing at any step is correct
    assert h.paid(a) <= 100000.0 + 0.005


def test_direct_payment_cannot_exceed_the_remaining(client, world):
    a = h.add_cert(world, 100000, no=1)
    _pay_form(client, a)
    assert h.paid(a) == 100000.0
    fake_db.execute("UPDATE [Certificates] SET [Status] = 'مُدخَل' WHERE [CertificateID] = ?", (a,))     # a wrong manual change of the status
    _pay_form(client, a)
    assert h.paid(a) == 100000.0 and len(h.payments(a)) == 1


def test_direct_payment_never_records_a_negative_amount(client, world):
    a = h.add_cert(world, 100000, no=1)
    fake_db.execute("INSERT INTO [Payments] ([CertificateID], [SubcontractorID], [PaidAt], [Amount], [CreatedAt]) VALUES (?, ?, ?, 60000, ?)",
                    (a, world, datetime.now(), datetime.now()))
    fake_db.execute("UPDATE [Certificates] SET [TotalInclVat] = 50000 WHERE [CertificateID] = ?", (a,))       # remaining is now -10,000
    _pay_form(client, a)
    assert all(p["Amount"] > 0 for p in h.payments(a))


# ------------------------------------------------------------------ C-02 a certificate that has money on it is frozen
def _partly_paid(world):
    import cycles_service as cy
    a = h.add_cert(world, 100000, no=1)
    cid = h.cycle_to_execution({a: (cy.D_PART, 60000)})
    cy.set_group_execution(cid, world, cy.EX_DONE, datetime.now(), "REF", "", "tester")
    assert h.cert_row(a)["Status"] == "مُدخَل" and h.paid(a) == 60000.0              # back to «entered»: the rest is still owed
    return a


def test_partly_paid_certificate_cannot_be_rejected(client, world):
    a = _partly_paid(world)
    client.post("/certificates/%d/reject" % a, data={"reason": "خطأ"})
    assert h.cert_row(a)["Status"] == "مُدخَل"


def test_an_unpaid_certificate_can_still_be_edited(client, world):
    """The guard must not block honest edits (and an edit must never crash)."""
    a = h.add_cert(world, 100000, no=1)
    client.post("/certificates/%d/edit" % a, data={"total_incl_vat": "90000", "supplied_materials": "0", "penalties": "0", "cert_no": "1"})
    assert h.cert_row(a)["TotalInclVat"] == 90000.0


def test_partly_paid_certificate_cannot_be_edited(client, world):
    a = _partly_paid(world)
    r = client.post("/certificates/%d/edit" % a, data={"total_incl_vat": "50000", "supplied_materials": "0", "penalties": "0", "cert_no": "1"})
    assert r.status_code in (302, 200), r.status_code
    assert h.cert_row(a)["TotalInclVat"] == 100000.0


# ------------------------------------------------------------------ C-03 a closed transaction moves no money
def test_closed_certificate_cannot_be_paid_or_unpaid_or_edited(client, world):
    a = h.add_cert(world, 100000, no=1)
    _pay_form(client, a)
    fake_db.execute("UPDATE [Certificates] SET [ClosedAt] = ? WHERE [CertificateID] = ?", (datetime.now(), a))
    client.post("/certificates/%d/unpay" % a)
    assert h.paid(a) == 100000.0
    b = h.add_cert(world, 70000, no=2, ClosedAt=datetime.now())
    _pay_form(client, b)
    assert h.paid(b) == 0
    client.post("/certificates/%d/edit" % b, data={"total_incl_vat": "1", "cert_no": "2"})
    assert h.cert_row(b)["TotalInclVat"] == 70000.0


# ------------------------------------------------------------------ C-04 deleting a cycle must not free a payment that belongs to another cycle
def test_deleting_an_old_cycle_does_not_free_an_entry_of_a_newer_cycle(world):
    import cycles_service as cy
    import ledger_service as ledger
    eid = fake_db.execute_insert("INSERT INTO [LedgerEntries] ([SubcontractorID], [Kind], [Category], [Description], [Amount], [Status], [CycleID], [CreatedAt]) "
                                 "VALUES (?, ?, ?, 'x', 50000, ?, 99, ?)", (world, ledger.K_CREDIT, ledger.CAT_PAYMENT, ledger.S_PENDING, datetime.now()))
    old = fake_db.execute_insert("INSERT INTO [PaymentCycles] ([CycleNo], [Status], [CreatedAt]) VALUES (7, ?, ?)", (cy.ST_DONE, datetime.now()))
    fake_db.execute("INSERT INTO [CycleDecisions] ([CycleID], [EntryID], [DecisionType], [RequestedAmount], [ApprovedAmount]) VALUES (?, ?, 'تأجيل', 50000, 0)", (old, eid))
    try:
        cy.delete_cycle(old, "tester")
    except cy.CycleError:
        pass
    assert fake_db.scalar("SELECT [CycleID] FROM [LedgerEntries] WHERE [EntryID] = ?", (eid,)) == 99


# ------------------------------------------------------------------ C-05 a database hiccup must never turn «paid» into zero
def test_a_database_error_is_not_swallowed(world, monkeypatch):
    import contracts_service as cs
    real = fake_db.query

    def flaky(sql, params=()):
        if "FROM [Payments]" in sql:
            raise RuntimeError("[ODBC] could not use; file already in use")
        return real(sql, params)
    monkeypatch.setattr(fake_db, "query", flaky)
    with pytest.raises(RuntimeError):
        cs.load()


def test_a_missing_table_is_still_tolerated(world, monkeypatch):
    import contracts_service as cs
    fake_db.execute("DROP TABLE [Payments]")
    assert cs.load()[3] == []


# ------------------------------------------------------------------ closing is the owner's free choice, but never by accident
def _ready_to_close(world):
    a = h.add_cert(world, 100000, no=1)
    for kind in ("نموذج طلب الصرف", "المستخلص المعتمد", "الاتفاقية", "الفاتورة الضريبية", "سند الصرف"):
        fake_db.execute("INSERT INTO [Documents] ([EntityType], [EntityKey], [DocType], [IsCurrent], [OriginalName], [StoredName], [RelPath], [UploadedAt]) "
                        "VALUES ('CERT', ?, ?, 1, 'f.pdf', 'f.pdf', 'x/f.pdf', ?)", (str(a), kind, datetime.now()))
    return a


def test_closing_an_unpaid_transaction_needs_an_explicit_ok(client, world):
    a = _ready_to_close(world)
    client.post("/archive/%d/close" % a)
    assert not h.cert_row(a).get("ClosedAt")                         # no confirmation -> not closed
    client.post("/archive/%d/close" % a, data={"unpaid_ok": "1"})
    assert h.cert_row(a).get("ClosedAt")                             # confirmed -> closed (the owner's free choice)
    assert fake_db.query("SELECT * FROM [AuditLog] WHERE [ActionName] = 'archive_close_unpaid'")


def test_closing_a_paid_transaction_needs_no_extra_step(client, world):
    a = _ready_to_close(world)
    _pay_form(client, a)
    client.post("/archive/%d/close" % a)
    assert h.cert_row(a).get("ClosedAt")


def test_archive_page_shows_the_unpaid_warning_in_the_close_button(client, world):
    a = _ready_to_close(world)
    html = client.get("/archive?cert=%d" % a).get_data(as_text=True)
    assert "unpaid_ok" in html and "100,000.00" in html


# ------------------------------------------------------------------ M-03: forms must refuse NaN / negative / absurd amounts
def test_ledger_refuses_nan_and_negative_amounts(client, world):
    for bad in ("nan", "-500", "1e999", "abc"):
        client.post("/subcontractors/%d/ledger/new" % world, data={"kind": "دائن", "category": "دفعة مباشرة", "description": "x", "amount": bad, "status": "executed"})
    assert fake_db.scalar("SELECT COUNT(*) FROM [LedgerEntries]", default=0) == 0
    client.post("/subcontractors/%d/ledger/new" % world, data={"kind": "دائن", "category": "دفعة مباشرة", "description": "x", "amount": "1,500.50", "status": "executed"})
    assert fake_db.scalar("SELECT COUNT(*) FROM [LedgerEntries]", default=0) == 1


def test_certificate_form_refuses_nan(client, world):
    client.post("/subcontractors/%d/certificates" % world, data={"project": "م", "site": "ص", "total_incl_vat": "nan", "cert_no": "1"})
    assert fake_db.scalar("SELECT COUNT(*) FROM [Certificates]", default=0) == 0


def test_loading_many_certificates_is_not_quadratic(world):
    """Performance guard (audit M8): grouping payments once must give the same figures as before."""
    import contracts_service as cs
    ids = [h.add_cert(world, 1000, no=i + 1) for i in range(60)]
    for i in ids[:30]:
        fake_db.execute("INSERT INTO [Payments] ([CertificateID], [SubcontractorID], [PaidAt], [Amount], [CreatedAt]) VALUES (?, ?, ?, 400, ?)", (i, world, datetime.now(), datetime.now()))
        fake_db.execute("INSERT INTO [Payments] ([CertificateID], [SubcontractorID], [PaidAt], [Amount], [CreatedAt]) VALUES (?, ?, ?, 100, ?)", (i, world, datetime.now(), datetime.now()))
    certs = {g["CertificateID"]: g for g in cs.load()[2]}
    assert certs[ids[0]]["Paid"] == 500.0 and certs[ids[0]]["Remaining"] == 500.0
    assert certs[ids[-1]]["Paid"] == 0.0 and certs[ids[-1]]["Remaining"] == 1000.0

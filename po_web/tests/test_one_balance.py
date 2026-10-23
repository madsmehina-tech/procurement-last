# -*- coding: utf-8 -*-
"""One number for «what is still owed to the contractor»: the account statement, the figures on the contractor page and the summary at the
top of his page must agree in every situation (audit M-06)."""
from datetime import datetime

import pytest

import conftest
import fake_db
import helpers as h
import ledger_service as ls
import contracts_service as cs


def _pay(cert_id, amount):
    fake_db.execute_insert("INSERT INTO [Payments] ([CertificateID], [Amount], [PaidAt]) VALUES (?, ?, ?)", (cert_id, amount, datetime.now()))


def _entry(sub, kind, category, amount):
    ls.add_entry(sub, kind, category, "حركة اختبار", amount, datetime.now(), "", "", True, "", "tester")


def _figures(sub):
    subs, contracts, certs, pays = cs.load()
    fig = cs.sub_figures(sub, contracts, certs)
    owed = -ls.statement(sub)["balance"]
    from routes.subcontractors import _summary
    my = [g for g in certs if g["SubcontractorID"] == sub]
    summ = _summary(sub, [], my, pays, fig)
    return fig["remaining"], owed, summ["remaining"], fig["paid"], summ["paid"]


@pytest.fixture(autouse=True)
def _schema():
    conftest.build_schema()


def _agree(sub):
    fig_rem, owed, summ_rem, fig_paid, summ_paid = _figures(sub)
    assert fig_rem == pytest.approx(owed, abs=0.01)
    assert summ_rem == pytest.approx(owed, abs=0.01)
    assert summ_paid == pytest.approx(fig_paid, abs=0.01)
    return owed


def test_plain_certificate_with_a_part_payment():
    s = h.add_sub(); c = h.add_cert(s, 1000); _pay(c, 400)
    assert _agree(s) == pytest.approx(600)


def test_a_rejected_certificate_that_already_received_money():
    s = h.add_sub(); c = h.add_cert(s, 1000, status="مرفوض"); _pay(c, 300)
    assert _agree(s) == pytest.approx(-300)          # nothing is due, 300 was paid: the contractor owes us


def test_an_old_certificate_paid_before_payments_were_recorded():
    s = h.add_sub(); h.add_cert(s, 800, status="مصروف")
    assert _agree(s) == pytest.approx(0)


def test_manual_debit_and_direct_payment_entries():
    s = h.add_sub(); h.add_cert(s, 1000)
    _entry(s, "مدين", "رد خصم", 100)
    _entry(s, "دائن", "دفعة مباشرة", 250)
    assert _agree(s) == pytest.approx(850)


def test_settlement_line_when_closing_with_a_balance():
    s = h.add_sub(); c = h.add_cert(s, 1000); _pay(c, 600)
    with __import__("db").transaction() as tx:
        ls.settle_on_close(tx, h.cert_row(c), 400, "tester")
    assert _agree(s) == pytest.approx(0)

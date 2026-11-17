# -*- coding: utf-8 -*-
"""v29.0 (the user's notes of 17/11): a payment order of a cycle IS a general voucher, and every printed paper carries its print date/time
in the page corner (which also stops the browser from adding its own header and footer)."""
import re

import pytest

import conftest
import cycles_service as cy
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


def _cycle_with_two_contractors():
    s1, s2 = h.add_sub("مقاول ألف"), h.add_sub("مقاول باء")
    a = h.add_cert(s1, total=15525.0, no=11)
    b = h.add_cert(s2, total=60000.0, no=3)
    c = h.add_cert(s2, total=42507.41, no=4)
    cid = h.cycle_to_execution({a: (cy.D_FULL, None), b: (cy.D_FULL, None), c: (cy.D_FULL, None)})
    return cid, s1, s2


def _vouchers():
    return fake_db.query("SELECT * FROM [PaymentVouchers] ORDER BY [VoucherID]")


def test_printing_a_payment_order_makes_the_next_voucher_and_prints_it_on_the_voucher_form(client):
    cid, s1, s2 = _cycle_with_two_contractors()
    html = client.get("/cycles/%d/subs/%d/order" % (cid, s2)).get_data(as_text=True)
    v = _vouchers()
    assert len(v) == 1 and v[0]["VoucherNo"] == "PR-0001" and v[0]["SubcontractorID"] == s2 and v[0]["Net"] == 102507.41
    assert v[0]["SourceRef"] == "CYCLE:%d:%d" % (cid, s2) and v[0]["PostChoice"] == vs.NO and v[0]["RequestType"] == "صرف مستخلصات (دورة صرف)"
    assert "طلب صرف" in html and "PR-0001" in html and "مستخلص رقم 3" in html and "مستخلص رقم 4" in html and "مائة واثنان ألفاً" in html
    # the same order printed again = the same voucher, the same number
    client.get("/cycles/%d/subs/%d/order" % (cid, s2))
    assert len(_vouchers()) == 1
    # printing all the orders: the one not printed yet takes the NEXT number, in the order of printing
    html = client.get("/cycles/%d/orders" % cid).get_data(as_text=True)
    nos = {r["SubcontractorID"]: r["VoucherNo"] for r in _vouchers()}
    assert nos == {s2: "PR-0001", s1: "PR-0002"} and html.count('class="vp-page"') == 2


def test_a_typed_voucher_and_a_cycle_voucher_share_one_numbering(client):
    from test_voucher_posting import _form
    sup = fake_db.execute_insert("INSERT INTO [Suppliers] ([Name], [CreatedAt]) VALUES ('مورد', Now())")
    client.post("/vouchers/new", data=_form("sup", sup, 100, choice="no"))
    cid, s1, _s2 = _cycle_with_two_contractors()
    client.get("/cycles/%d/subs/%d/order" % (cid, s1))
    client.post("/vouchers/new", data=_form("sup", sup, 50, choice="no"))
    assert [r["VoucherNo"] for r in _vouchers()] == ["PR-0001", "PR-0002", "PR-0003"]


def test_it_follows_its_cycle_and_never_touches_the_statement(client):
    cid, s1, _s2 = _cycle_with_two_contractors()
    client.get("/cycles/%d/subs/%d/order" % (cid, s1))
    v = _vouchers()[0]
    cycle = fake_db.query("SELECT [Status] FROM [PaymentCycles] WHERE [CycleID] = ?", (cid,))[0]
    expected = vs.ST_APPROVED if cycle["Status"] in (cy.ST_APPROVED, cy.ST_DONE) else vs.ST_DRAFT
    assert v["Status"] == expected and not v.get("PostedAt")
    assert fake_db.scalar("SELECT COUNT(*) FROM [LedgerEntries] WHERE [PayNo] = ?", (v["VoucherNo"],)) == 0
    # no edit and no approval from the voucher screens: the cycle decides
    page = client.get("/vouchers/%d" % v["VoucherID"]).get_data(as_text=True)
    assert "دورة الصرف رقم" in page and ">تعديل<" not in page
    client.get("/vouchers/%d/edit" % v["VoucherID"])
    if v["Status"] == vs.ST_DRAFT:
        client.post("/vouchers/%d/approve" % v["VoucherID"])
        assert _vouchers()[0]["Status"] == vs.ST_DRAFT
    # listed with the other vouchers, marked with its cycle
    lst = client.get("/vouchers").get_data(as_text=True)
    assert v["VoucherNo"] in lst and "دورة " in lst


def test_a_changed_decision_updates_the_same_voucher_and_a_cancelled_one_is_replaced(client):
    cid, s1, _s2 = _cycle_with_two_contractors()
    client.get("/cycles/%d/subs/%d/order" % (cid, s1))
    vid = _vouchers()[0]["VoucherID"]
    fake_db.execute("UPDATE [PaymentVouchers] SET [Net] = 1, [ItemsJson] = '[]' WHERE [VoucherID] = ?", (vid,))     # as if the decision had changed
    client.get("/cycles/%d/subs/%d/order" % (cid, s1))
    v = _vouchers()
    assert len(v) == 1 and v[0]["Net"] == 15525.0 and v[0]["VoucherNo"] == "PR-0001"
    fake_db.execute("UPDATE [PaymentVouchers] SET [Status] = ? WHERE [VoucherID] = ?", (vs.ST_CANCELLED, vid))
    client.get("/cycles/%d/subs/%d/order" % (cid, s1))
    assert [r["VoucherNo"] for r in _vouchers()] == ["PR-0001", "PR-0002"]


def test_every_printed_paper_has_its_print_time_in_the_page_corner(client):
    cid, s1, _s2 = _cycle_with_two_contractors()
    client.get("/cycles/%d/subs/%d/order" % (cid, s1))
    vid = _vouchers()[0]["VoucherID"]
    fake_db.execute("INSERT INTO [PurchaseOrders] ([PONumber], [OrderDate], [SupplierName], [Project], [TotalAmount], [CreatedAt]) VALUES ('PO-T', Now(), 'م', 'م', 1, Now())")
    pages = ["/orders/PO-T/print", "/vouchers/%d/print" % vid, "/cycles/%d/orders" % cid, "/cycles/%d/print" % cid,
             "/subcontractors/%d/statement/print" % s1, "/orders", "/vouchers"]
    for url in pages:
        html = client.get(url).get_data(as_text=True)
        assert re.search(r'@bottom-right \{ content: "تاريخ ووقت الطباعة: \d\d/\d\d/\d{4} - \d\d:\d\d"', html), url
        assert "beforeprint" in html, url
    paper = client.get("/vouchers/%d/print" % vid).get_data(as_text=True)
    assert "@media print { .vp-foot { display: none; } }" in paper              # one time on paper, not two

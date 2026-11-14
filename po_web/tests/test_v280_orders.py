# -*- coding: utf-8 -*-
"""v28.0 - the purchase order: optional VAT (added / included / none) with its percentage, other currencies with a rate, and the new printed form."""
from datetime import datetime

import pytest

import conftest
import fake_db
import order_math
import po_files
import signatures


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


def _form(po="PO-2026-900", **over):
    f = {"po_number": po, "supplier": "مصنع اليمامية", "project": "حدائق الفرسان", "order_date": "2026-11-14", "discount": "0",
         "item_name": ["عمود إنارة"], "item_unit": ["عدد"], "item_qty": ["10"], "item_price": ["115"]}
    f.update(over)
    return f


def _order(po="PO-2026-900"):
    return fake_db.query("SELECT * FROM [PurchaseOrders] WHERE [PONumber] = ?", (po,))[0]


def test_the_three_vat_rules():
    lines = [(10, 115)]
    assert order_math.order_totals(lines) == {"subtotal": 1150.0, "vat": 172.5, "total_with_vat": 1322.5, "discount": 0.0, "grand": 1322.5, "mode": "add", "pct": 15.0}
    incl = order_math.order_totals(lines, 0, "incl", 15)
    assert (incl["total_with_vat"], incl["subtotal"], incl["vat"], incl["grand"]) == (1150.0, 1000.0, 150.0, 1150.0)
    none = order_math.order_totals(lines, 50, "none", 15)
    assert (none["vat"], none["grand"], none["pct"]) == (0.0, 1100.0, 0.0)
    assert order_math.order_totals(lines, 0, "add", 5)["vat"] == 57.5
    assert order_math.order_totals(lines, 0, "weird", "abc")["grand"] == 1322.5          # anything unknown behaves like every order before 28.0


def test_an_order_saved_the_old_way_is_unchanged(client):
    client.post("/orders/new", data=_form())
    o = _order()
    assert (o["TotalAmount"], o["TotalCurrency"], o["VatMode"], o["VatPct"], o["CurrencyCode"], o["ExchangeRate"]) == (1322.5, 1322.5, "add", 15.0, "SAR", 1.0)


def test_prices_that_include_the_vat_and_an_order_without_vat(client):
    client.post("/orders/new", data=_form("PO-A", vat_mode="incl"))
    client.post("/orders/new", data=_form("PO-B", vat_mode="none"))
    client.post("/orders/new", data=_form("PO-C", vat_mode="add", vat_pct="5"))
    assert [_order(p)["TotalAmount"] for p in ("PO-A", "PO-B", "PO-C")] == [1150.0, 1150.0, 1207.5]
    a = client.get("/orders/PO-A/print").get_data(as_text=True)
    assert "الأسعار شاملة الضريبة" in a and "الضريبة (" not in a and "1150.00" in a            # one line, no breakdown
    b = client.get("/orders/PO-B/print").get_data(as_text=True)
    assert "الضريبة" not in b and "1150.00" in b                                              # the VAT lines simply disappear
    c = client.get("/orders/PO-C/print").get_data(as_text=True)
    assert "الضريبة (5%)" in c and "57.50" in c


def test_a_dollar_order_prints_dollars_and_is_counted_in_riyals(client):
    r = client.post("/orders/new", data=_form("PO-USD", currency="USD", exchange_rate="3.75"))
    o = _order("PO-USD")
    assert (o["CurrencyCode"], o["ExchangeRate"], o["TotalCurrency"], o["TotalAmount"]) == ("USD", 3.75, 1322.5, 4959.38)
    page = client.get("/orders/PO-USD/print").get_data(as_text=True)
    assert "دولار" in page and "1322.50" in page and "دولار أمريكي" in page and "ريالاً" not in page
    bad = client.post("/orders/new", data=_form("PO-EUR", currency="EUR")).get_data(as_text=True)
    assert "سعر الصرف" in bad and fake_db.query("SELECT * FROM [PurchaseOrders] WHERE [PONumber] = 'PO-EUR'") == []


def test_editing_keeps_and_changes_the_vat_and_currency(client):
    client.post("/orders/new", data=_form("PO-E", vat_mode="incl", currency="USD", exchange_rate="3.75"))
    page = client.get("/orders/PO-E/edit").get_data(as_text=True)
    assert 'value="incl" checked' in page and 'value="USD" selected' in page and 'value="3.75"' in page
    client.post("/orders/PO-E/edit", data=_form("PO-E", vat_mode="none", currency="SAR", loaded_version="0"))
    o = _order("PO-E")
    assert (o["VatMode"], o["CurrencyCode"], o["ExchangeRate"], o["TotalAmount"]) == ("none", "SAR", 1.0, 1150.0)


def test_the_printed_order_has_the_configured_signatures_with_names_and_the_new_layout(client):
    client.post("/orders/new", data=_form())
    page = client.get("/orders/PO-2026-900/print").get_data(as_text=True)
    for t in signatures.DEFAULT_PO:
        assert t in page
    assert "width: 108px" in page and "height: 104px" in page and "inline-grid" in page          # bigger logo, taller boxes, number / date / hijri together
    signatures.save(signatures.PO, [{"title": "مراجع الجودة", "name": "م. سارة"}] + [{"title": t, "name": ""} for t in signatures.DEFAULT_PO])
    page = client.get("/orders/PO-2026-900/print").get_data(as_text=True)
    assert "مراجع الجودة" in page and "م. سارة" in page and page.count('<table class="sigs">') == 2
    assert page.index("مراجع الجودة") < page.index(signatures.DEFAULT_PO[0])                       # the added box is on the row above


def test_signatures_are_edited_in_the_company_settings(client):
    fake_db.execute("INSERT INTO [CompanyProfile] ([ProfileID], [LegalName], [UpdatedAt]) VALUES (1, 'شركة الهضب', ?)", (datetime.now(),))
    page = client.get("/settings/company").get_data(as_text=True)
    assert 'name="po_sign_title"' in page and 'name="voucher_sign_title"' in page and "إضافة توقيع" in page
    client.post("/settings/company", data={"legalname": "شركة الهضب", "po_sign_title": ["أ", "ب"], "po_sign_name": ["اسم أ", ""], "voucher_sign_title": ["ج"], "voucher_sign_name": ["اسم ج"]})
    assert signatures.get(signatures.PO) == [{"title": "أ", "name": "اسم أ"}, {"title": "ب", "name": ""}] and signatures.get(signatures.VOUCHER) == [{"title": "ج", "name": "اسم ج"}]
    r = client.post("/settings/company", data={"legalname": "شركة الهضب", "po_sign_title": ["", ""], "po_sign_name": ["", ""], "voucher_sign_title": ["ج"], "voucher_sign_name": [""]}, follow_redirects=True)
    assert "توقيعاً واحداً" in r.get_data(as_text=True) and len(signatures.get(signatures.PO)) == 2          # refused: the lists are unchanged

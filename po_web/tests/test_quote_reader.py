# -*- coding: utf-8 -*-
"""Reading a supplier quotation must never lose a line or invent a price (audit C-06 / F-01, F-02)."""
import conftest  # noqa: F401
import quote2po as q


def _items(md):
    return {i["name"]: (i["qty"], i["price"]) for i in q.parse_quote(md)["items"]}


TABLE = """Acme Trading Co
Quotation No: Q-77

| No | Description | Unit | Qty | Unit Price | Total |
|---|---|---|---|---|---|
{rows}
| | Sub Total | | | | {sub} |
| | VAT 15% | | | | {vat} |
| | Grand Total | | | | {grand} |
"""


def test_items_containing_vat_or_net_are_not_mistaken_for_totals():
    rows = ("| 1 | Excavation works | m3 | 10 | 50 | 500 |\n| 2 | Ethernet cable Cat6 | roll | 2 | 300 | 600 |\n"
            "| 3 | Renovation of facade | m2 | 5 | 100 | 500 |\n| 4 | Cabinet 19\" 42U | pc | 1 | 800 | 800 |\n| 5 | Cement bags | bag | 100 | 20 | 2000 |")
    got = _items(TABLE.format(rows=rows, sub="4400", vat="660", grand="5060"))
    assert set(got) == {"Excavation works", "Ethernet cable Cat6", "Renovation of facade", 'Cabinet 19" 42U', "Cement bags"}


def test_the_real_totals_rows_are_still_dropped():
    rows = "| 1 | Cement bags | bag | 100 | 20 | 2000 |"
    got = _items(TABLE.format(rows=rows, sub="2000", vat="300", grand="2300"))
    assert list(got) == ["Cement bags"]


def test_an_arabic_set_of_items_is_an_item_not_a_total():
    md = ("شركة الأفق\nعرض سعر رقم 5\n\n| م | البيان | الوحدة | الكمية | سعر الوحدة | الإجمالي |\n|---|---|---|---|---|---|\n"
          "| 1 | مجموعة أدوات سباكة | عدد | 3 | 120 | 360 |\n| | المجموع | | | | 360 |\n")
    assert "مجموعة أدوات سباكة" in _items(md)


def test_a_discount_percentage_is_never_read_as_a_price():
    md = """Acme Co
Quotation No: Q-8

| No | Description | Unit | Qty | Unit Price | Discount | Total |
|---|---|---|---|---|---|---|
| 1 | Paint | pail | 10 | 100 | 10% | 900 |
"""
    got = _items(md)
    assert got["Paint"][0] == 10 and got["Paint"][1] == 100, got

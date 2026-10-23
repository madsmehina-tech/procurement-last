# -*- coding: utf-8 -*-
"""The stored total of an order and the printed total must be the same number, always (audit M-07 / F-09). The discount is taken AFTER the VAT (the owner's decision)."""
import random

import conftest  # noqa: F401
import order_math as om
import po_files


def test_the_owners_discount_rule_is_kept():
    t = om.order_totals([(1, 10000)], 1000)
    assert (t["subtotal"], t["vat"], t["total_with_vat"], t["grand"]) == (10000.0, 1500.0, 11500.0, 10500.0)      # VAT on 10,000, then minus 1,000


def test_half_up_rounding_like_an_invoice():
    assert om.line_total(3, 0.335) == 1.01          # 1.005 -> 1.01 (python's round() gives 1.0)
    assert om.money2(100.005) == 100.01


def test_printed_lines_add_up_to_the_printed_subtotal():
    items = [{"Qty": 3, "UnitPrice": 0.335, "ItemName": "x", "Unit": "u", "LineNo": i} for i in range(1, 4)]
    ctx = po_files.print_context({"Discount": 0}, items)
    assert round(sum(i["LineTotal"] for i in ctx["items"]), 2) == ctx["subtotal"] == 3.03


def test_stored_and_printed_totals_never_differ():
    rnd = random.Random(1)
    for _ in range(3000):
        lines = [(round(rnd.uniform(0.1, 500), rnd.choice((0, 1, 2, 3))), round(rnd.uniform(0.01, 999), rnd.choice((1, 2, 3, 4)))) for _ in range(rnd.randint(1, 8))]
        disc = round(rnd.uniform(0, 50), 2)
        stored = om.order_totals(lines, disc)["grand"]
        items = [{"Qty": q, "UnitPrice": p, "ItemName": "x", "Unit": "u", "LineNo": i} for i, (q, p) in enumerate(lines, 1)]
        printed = po_files.print_context({"Discount": disc}, items)["grand"]
        assert stored == printed

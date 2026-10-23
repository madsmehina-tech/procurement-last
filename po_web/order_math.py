# -*- coding: utf-8 -*-
"""
order_math.py - the ONE place that adds up a purchase order. The stored total (database, dashboards, supplier totals), the printed order and the
order page all use these functions, so the same order can never show two different totals.

Rules (the owner's decisions):
  * each line total = quantity x unit price, rounded to 2 decimals ("half up", like a calculator and an invoice - not "banker's rounding");
  * subtotal = the sum of the ROUNDED line totals (so the printed lines always add up to the printed subtotal);
  * VAT = 15% of the subtotal, rounded to 2 decimals;
  * the discount is taken AFTER the VAT (the owner confirmed this is intended).
"""
from decimal import ROUND_HALF_UP, Decimal

VAT_RATE = Decimal("0.15")
_CENT = Decimal("0.01")


def _d(x):
    return Decimal(str(x if x is not None else 0))


def money2(x):
    """x rounded to 2 decimals, half up, as a float."""
    return float(_d(x).quantize(_CENT, rounding=ROUND_HALF_UP))


def line_total(qty, price):
    return money2(_d(qty) * _d(price))


def order_totals(lines, discount=0):
    """lines: iterable of (qty, unit_price). Returns {subtotal, vat, total_with_vat, discount, grand}."""
    subtotal = sum((_d(line_total(q, p)) for q, p in lines), Decimal(0)).quantize(_CENT, rounding=ROUND_HALF_UP)
    vat = (subtotal * VAT_RATE).quantize(_CENT, rounding=ROUND_HALF_UP)
    with_vat = subtotal + vat
    disc = _d(discount).quantize(_CENT, rounding=ROUND_HALF_UP)
    return {"subtotal": float(subtotal), "vat": float(vat), "total_with_vat": float(with_vat), "discount": float(disc), "grand": float(with_vat - disc)}


def with_line_totals(items):
    """The order lines as dicts that also carry LineTotal (what each line shows on screen and on paper)."""
    return [dict(it, LineTotal=line_total(it.get("Qty"), it.get("UnitPrice"))) for it in items]

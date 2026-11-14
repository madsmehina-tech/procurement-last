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


VAT_MODES = ("add", "incl", "none")          # the VAT is added to the prices | the prices already include it | no VAT at all


def vat_mode(value):
    return value if value in VAT_MODES else "add"


def vat_pct(value):
    """The VAT percentage of an order (15 when it was never set)."""
    try:
        p = float(value)
    except (TypeError, ValueError):
        return 15.0
    return p if 0 <= p <= 100 else 15.0


def order_totals(lines, discount=0, mode="add", pct=15):
    """lines: iterable of (qty, unit_price). Returns {subtotal, vat, total_with_vat, discount, grand, mode, pct}.
        add   subtotal = the lines; the VAT is added on top (the rule of every order before 28.0, with 15%)
        incl  the lines already INCLUDE the VAT: their sum is the total; the VAT inside it is shown apart
        none  no VAT: the total is the sum of the lines"""
    mode, rate = vat_mode(mode), _d(vat_pct(pct)) / Decimal(100)
    summed = sum((_d(line_total(q, p)) for q, p in lines), Decimal(0)).quantize(_CENT, rounding=ROUND_HALF_UP)
    if mode == "incl":
        with_vat = summed
        subtotal = (summed / (Decimal(1) + rate)).quantize(_CENT, rounding=ROUND_HALF_UP)
        vat = with_vat - subtotal
    elif mode == "none":
        subtotal, vat, with_vat = summed, Decimal(0), summed
    else:
        subtotal = summed
        vat = (subtotal * rate).quantize(_CENT, rounding=ROUND_HALF_UP)
        with_vat = subtotal + vat
    disc = _d(discount).quantize(_CENT, rounding=ROUND_HALF_UP)
    return {"subtotal": float(subtotal), "vat": float(vat), "total_with_vat": float(with_vat), "discount": float(disc), "grand": float(with_vat - disc),
            "mode": mode, "pct": float(vat_pct(pct)) if mode != "none" else 0.0}


def with_line_totals(items):
    """The order lines as dicts that also carry LineTotal (what each line shows on screen and on paper)."""
    return [dict(it, LineTotal=line_total(it.get("Qty"), it.get("UnitPrice"))) for it in items]

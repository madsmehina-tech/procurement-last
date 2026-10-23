# -*- coding: utf-8 -*-
"""A money amount typed in a form: finite, non-negative, two decimals, with a sensible ceiling (audit M-03)."""
import pytest

import conftest  # noqa: F401
import money_guards as mg


@pytest.mark.parametrize("text", ["nan", "NaN", "inf", "-inf", "1e999", "-5", "abc", "1,5,5x", "٫"])
def test_rejects_bad_amounts(text):
    with pytest.raises(mg.MoneyError):
        mg.parse_money(text)


@pytest.mark.parametrize("text,expected", [("1,250.50", 1250.5), ("100.005", 100.01), ("٣٤٥٫٥", 345.5), ("0", 0.0), ("", 0.0), ("  7 ", 7.0)])
def test_accepts_good_amounts(text, expected):
    assert mg.parse_money(text) == pytest.approx(expected)


def test_zero_can_be_refused_and_ceiling_is_enforced():
    with pytest.raises(mg.MoneyError):
        mg.parse_money("0", allow_zero=False)
    with pytest.raises(mg.MoneyError):
        mg.parse_money("1000000000")

# -*- coding: utf-8 -*-
"""The amount in words printed on purchase orders and contracts (audit M-09)."""
import pytest

import conftest  # noqa: F401
import arabic_numbers as a
import contract_gen as cg


@pytest.mark.parametrize("amount,expected", [
    (0, "فقط صفر ريال لا غير"),
    (1, "فقط ريال واحد لا غير"),
    (2, "فقط ريالان لا غير"),
    (3, "فقط ثلاثة ريالات لا غير"),
    (11, "فقط أحد عشر ريالاً لا غير"),
    (100, "فقط مائة ريال لا غير"),
    (1000, "فقط ألف ريال لا غير"),
    (2000, "فقط ألفا ريال لا غير"),
    (46000, "فقط ستة وأربعون ألف ريال لا غير"),
    (125000.5, "فقط مائة وخمسة وعشرون ألف ريال وخمسون هللة لا غير"),
    (6999.98, "فقط ستة آلاف وتسع مائة وتسعة وتسعون ريالاً وثمانية وتسعون هللة لا غير"),   # the company's own printed style, unchanged
    (0.5, "فقط خمسون هللة لا غير"),
])
def test_purchase_order_words(amount, expected):
    assert a.amount_to_words(amount) == expected


@pytest.mark.parametrize("amount", [-1, 1_000_000_000, float("nan"), float("inf")])
def test_out_of_range_amounts_are_refused_cleanly(amount):
    with pytest.raises(ValueError):
        a.amount_to_words(amount)


@pytest.mark.parametrize("amount,expected", [
    (46000, "فقط ستة وأربعون ألف ريال سعودي لا غير"),
    (1, "فقط ريال سعودي واحد لا غير"),
    (3, "فقط ثلاثة ريالات سعودية لا غير"),
    (300, "فقط ثلاثمائة ريال سعودي لا غير"),
    (500, "فقط خمسمائة ريال سعودي لا غير"),
    (2000, "فقط ألفا ريال سعودي لا غير"),
])
def test_contract_words(amount, expected):
    assert cg.words_sar(amount) == expected

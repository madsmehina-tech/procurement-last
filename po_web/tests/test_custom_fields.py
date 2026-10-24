# -*- coding: utf-8 -*-
"""A custom field (cf_...) must never replace a value the system computes - the amount in words must always match the contract value (audit M-24)."""
from datetime import datetime

import pytest

import conftest
import contract_gen as cg
import fake_db
import helpers as h


@pytest.fixture()
def cid():
    conftest.build_schema()
    sub = h.add_sub()
    c = fake_db.execute_insert("INSERT INTO [Contracts] ([SubcontractorID], [Origin], [Status], [ContractNo], [PriceModel], [CreatedAt]) VALUES (?, 'مولَّد', 'مسودة', 'SC-1', 'مقطوعية', ?)", (sub, datetime.now()))
    fake_db.execute("INSERT INTO [ContractLines] ([ContractID], [Kind], [LineNo], [Description], [Qty], [UnitPrice]) VALUES (?, 'سعر', 1, 'أعمال', 1, 100000)", (c,))
    return c


def test_a_custom_field_with_a_standard_name_does_not_replace_the_computed_value(cid):
    fake_db.execute("INSERT INTO [ContractData] ([ContractID], [FieldKey], [FieldValue]) VALUES (?, 'القيمة_بالحروف', 'مليون ريال')", (cid,))
    fake_db.execute("INSERT INTO [ContractData] ([ContractID], [FieldKey], [FieldValue]) VALUES (?, 'اسم_المشرف', 'خالد')", (cid,))
    v = cg.build_values(cid)
    assert "مليون" not in v["القيمة_بالحروف"] and "ألف" in v["القيمة_بالحروف"]          # still the amount really in the contract
    assert v["اسم_المشرف"] == "خالد"                                                      # a real custom field still works

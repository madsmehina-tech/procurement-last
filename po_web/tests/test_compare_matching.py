# -*- coding: utf-8 -*-
"""Comparing quotations: different sizes are never matched automatically, and a missing price is never the cheapest (audit C-07 / F-03, F-04, F-05)."""
import pytest

import conftest  # noqa: F401
import compare_service as cs

AUTO = cs.AUTO_SCORE


@pytest.mark.parametrize("a,b", [
    ("بلوك 20×20×40", "بلوك 15×20×40"),
    ("ماسورة 1/2", "ماسورة 1"),
    ("ماسورة 1/2", "ماسورة 1 1/2"),
    ("كابل 4×16", "كابل 16"),
    ("كابل 4×16", "كابل 6×25"),
])
def test_different_sizes_never_reach_the_automatic_match_score(a, b):
    assert cs.similarity(a, b) < AUTO


@pytest.mark.parametrize("a,b", [
    ("بلوك 20×20×40", "بلوك 20x20x40"),
    ("ماسورة حديد 6 بوصة", "ماسورة حديد 6 بوصه"),
    ("اسمنت بورتلاندي", "اسمنت بورتلاندي"),
])
def test_the_same_item_written_differently_is_still_matched(a, b):
    assert cs.similarity(a, b) >= AUTO


def test_number_tuples_keep_order_and_fractions():
    assert cs._number_tuple("بلوك 20×20×40") == (20.0, 20.0, 40.0)
    assert cs._number_tuple("ماسورة 1 1/2") == (1.5,)
    assert cs._number_tuple("ماسورة 1/2") == (0.5,)
    assert cs._number_tuple("بلا ارقام") == ()


# ---------------------------------------------------------------- the analysis: missing price / other unit are MISSING, and need a written reason to award
from datetime import datetime  # noqa: E402

import fake_db  # noqa: E402


def _comparison(offers):
    """offers: {party: [(item_name, unit, qty, price), ...]} - every quote is «VAT not included», lines are matched and confirmed."""
    conftest.build_schema()
    cid = fake_db.execute_insert("INSERT INTO [Comparisons] ([Title], [Kind], [Status], [Confirmed], [CreatedAt]) VALUES ('t', 'مواد', 'مفتوحة', 1, ?)", (datetime.now(),))
    items = {}
    for lines in offers.values():
        for name, *_ in lines:
            if name not in items:
                items[name] = fake_db.execute_insert("INSERT INTO [ComparisonItems] ([CompareID], [SortNo], [Name], [RequiredQty], [QtyFixed]) VALUES (?, ?, ?, 10, 1)", (cid, len(items) + 1, name))
    qids = {}
    for party, lines in offers.items():
        qid = qids[party] = fake_db.execute_insert("INSERT INTO [Quotes] ([CompareID], [PartyKind], [PartyName], [VatIncluded], [CreatedAt]) VALUES (?, 'supplier', ?, 'no', ?)", (cid, party, datetime.now()))
        for i, (name, unit, qty, price) in enumerate(lines, 1):
            fake_db.execute("INSERT INTO [QuoteLines] ([QuoteID], [LineNo], [RawName], [Unit], [Qty], [UnitPrice], [ItemID], [Confidence], [Locked]) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1)",
                            (qid, i, name, unit, qty, price, items[name]))
    return cid, qids


def _sum(a, party):
    return next(s for s in a["summary"] if s["name"] == party)


def test_a_line_without_a_price_is_missing_not_cheap():
    cid, _q = _comparison({"أ": [("اسمنت", "كيس", 10, 20), ("حديد", "طن", 10, 0)],        # «أ» left the iron price empty (OCR could not read it)
                           "ب": [("اسمنت", "كيس", 10, 21), ("حديد", "طن", 10, 100)]})
    a = cs.analyze(cid)
    s_a, s_b = _sum(a, "أ"), _sum(a, "ب")
    assert s_a["complete"] == 50.0 and "حديد" in s_a["missing"]                              # counted as missing, not 100% complete
    assert s_a["adjusted"] > s_a["normalized"]                                                # completed at the other offer's average
    assert s_a["adjusted"] > s_b["adjusted"] - 1000 and s_a["adjusted"] >= 200 + 1000          # no longer wins with a fake total of 200


def test_a_price_in_another_unit_is_not_compared():
    cid, _q = _comparison({"أ": [("حديد", "كيلو", 10, 3)], "ب": [("حديد", "طن", 10, 3000)]})
    a = cs.analyze(cid)
    row = a["rows"][0]
    assert sum(1 for c in row["cells"].values() if c.get("lowest")) == 0                       # neither is flagged «lowest» by comparing kilo with ton


def test_awarding_an_offer_with_a_missing_price_needs_a_reason():
    cid, q = _comparison({"أ": [("اسمنت", "كيس", 10, 20), ("حديد", "طن", 10, 0)],
                          "ب": [("اسمنت", "كيس", 10, 21), ("حديد", "طن", 10, 100)]})
    with pytest.raises(cs.CompareError):
        cs.award(cid, q["أ"], "", "tester")
    cs.award(cid, q["أ"], "السعر سيُستكمل من المورد قبل التعاقد", "tester")                 # with a written reason it is allowed

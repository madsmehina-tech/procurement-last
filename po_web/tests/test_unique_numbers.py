# -*- coding: utf-8 -*-
"""The database itself must refuse a duplicate order / cycle number (audit M-01), and the migration must never break on existing duplicates."""
import sqlite3
from datetime import datetime

import pytest

import conftest
import fake_db


def _po(n):
    fake_db.execute("INSERT INTO [PurchaseOrders] ([PONumber], [CreatedAt]) VALUES (?, ?)", (n, datetime.now()))


def test_a_second_order_with_the_same_number_is_refused_by_the_database():
    conftest.build_schema()
    _po("PO-1")
    with pytest.raises(sqlite3.IntegrityError):
        _po("PO-1")


def test_two_cycles_with_the_same_number_are_refused():
    conftest.build_schema()
    fake_db.execute("INSERT INTO [PaymentCycles] ([CycleNo], [Status], [CreatedAt]) VALUES (1, 'مسودة', ?)", (datetime.now(),))
    with pytest.raises(sqlite3.IntegrityError):
        fake_db.execute("INSERT INTO [PaymentCycles] ([CycleNo], [Status], [CreatedAt]) VALUES (1, 'مسودة', ?)", (datetime.now(),))


def test_existing_duplicates_never_break_the_migration(capsys):
    import importlib
    conftest.build_schema()
    fake_db.execute("DROP INDEX ux_po_number")
    _po("PO-9")
    _po("PO-9")                                                       # a duplicate that is already in the data
    m = importlib.import_module("migrations.m026_unique_numbers")
    m.main()                                                          # must not raise
    out = capsys.readouterr().out
    assert "PO-9" in out and "تكرار" in out                           # tells the person exactly what to fix
    assert fake_db.scalar("SELECT COUNT(*) FROM [PurchaseOrders]") == 2   # and changed nothing


def test_running_it_twice_is_harmless():
    import importlib
    conftest.build_schema()
    m = importlib.import_module("migrations.m026_unique_numbers")
    m.main()
    m.main()

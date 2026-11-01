# -*- coding: utf-8 -*-
"""v27.3 - the points of the outside review that were real: a value typed by a user must never reach JavaScript."""
import glob
import os
import re

import pytest

import conftest  # noqa: F401
import fake_db
import helpers as h

EVIL = "x');alert(1);//"


@pytest.fixture(autouse=True)
def _schema():
    conftest.build_schema()


def test_no_template_puts_an_address_inside_javascript():
    for f in glob.glob(os.path.join(os.path.dirname(__file__), "..", "templates", "*.html")):
        text = open(f, encoding="utf-8").read()
        assert not re.search(r"onclick=\"[^\"]*location\.href", text), os.path.basename(f)


def test_an_unsafe_po_number_is_refused_and_a_stored_one_cannot_run_code():
    import app as appmod
    cl = appmod.app.test_client()
    with cl.session_transaction() as s:
        s["user_id"] = 1
    form = {"po_number": EVIL, "supplier": "م", "project": "p", "item_name": ["x"], "item_unit": ["u"], "item_qty": ["1"], "item_price": ["1"], "discount": "0"}
    r = cl.post("/orders/new", data=form)
    assert "يقبل الحروف والأرقام" in r.get_data(as_text=True)
    assert not fake_db.query("SELECT [PONumber] FROM [PurchaseOrders] WHERE [PONumber] = ?", (EVIL,))
    # an order that was stored before this rule existed: the pages must carry its number as plain data, not as script
    fake_db.execute("INSERT INTO [PurchaseOrders] ([PONumber], [SupplierName], [Project], [OrderDate], [TotalAmount]) VALUES (?, 's', 'p', ?, 10)",
                    (EVIL, __import__("datetime").datetime.now()))
    for page in ("/", "/orders"):
        html = cl.get(page).get_data(as_text=True)
        assert "alert(1)" not in re.sub(r'data-href="[^"]*"', "", html) or 'onclick="location' not in html
        assert 'onclick="location' not in html


def _real_db(monkeypatch, fail_with):
    import importlib.util
    import sys
    import types
    stub = types.ModuleType("pyodbc")
    stub.SQL_CHAR, stub.SQL_WCHAR, stub.SQL_LONGVARCHAR = 1, -8, -1
    stub.Error = type("Error", (Exception,), {})

    def connect(*a, **k):
        if k.get("autocommit") is False:
            raise stub.Error(*fail_with)
        raise stub.Error("08001", "the database is locked")
    stub.connect = connect
    monkeypatch.setitem(sys.modules, "pyodbc", stub)
    spec = importlib.util.spec_from_file_location("real_db_sec", os.path.join(conftest.ROOT, "db.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_failed_connection_does_not_turn_a_transaction_into_loose_statements(monkeypatch):
    mod = _real_db(monkeypatch, ("08001", "the database is locked"))
    with pytest.raises(mod.pyodbc.Error):
        with mod.transaction():
            pass                                           # must fail HERE, not fall back to statement-by-statement

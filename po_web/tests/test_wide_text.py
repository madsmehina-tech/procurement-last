# -*- coding: utf-8 -*-
"""A PC whose Windows ANSI code page is 1252 (English Windows) must still work with Arabic: the SQL text and the parameters travel as UTF-16.
Before, the first Arabic literal in a statement crashed with «'charmap' codec can't encode characters» (field report, v25.7)."""
import importlib.util
import os
import sqlite3
import sys
import types

import pytest

import conftest

_DB = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)


class _Conn:
    def __init__(self):
        self.enc = None                                   # (encoding, ctype) as pyodbc keeps it

    def setdecoding(self, *a, **k):
        pass

    def setencoding(self, encoding=None, ctype=None):
        self.enc = (encoding, ctype)

    def cursor(self):
        conn = self

        class Cur:
            def __init__(self):
                self._c = _DB.cursor()

            def execute(self, sql, params=()):
                if conn.enc and conn.enc[1] != -8:                       # narrow: the driver converts the text with the PC's code page, strictly
                    sql.encode(conn.enc[0])
                    for p in params:
                        if isinstance(p, str):
                            p.encode(conn.enc[0])
                self._c.execute(sql, params)
                return self

            def setinputsizes(self, sizes):
                pass

            def fetchall(self):
                return self._c.fetchall()

            @property
            def description(self):
                return self._c.description

            def close(self):
                pass
        return Cur()

    def commit(self):
        pass

    def close(self):
        pass


def _db(monkeypatch, acp):
    stub = types.ModuleType("pyodbc")
    stub.SQL_CHAR, stub.SQL_WCHAR, stub.SQL_LONGVARCHAR, stub.SQL_WLONGVARCHAR = 1, -8, -1, -10
    stub.Error = type("Error", (Exception,), {})
    stub.connect = lambda *a, **k: _Conn()
    monkeypatch.setitem(sys.modules, "pyodbc", stub)
    spec = importlib.util.spec_from_file_location("db_wide_under_test", os.path.join(conftest.ROOT, "db.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._get_acp = lambda: acp
    _DB.execute("DROP TABLE IF EXISTS po")
    _DB.execute("CREATE TABLE po (name TEXT, status TEXT)")
    return mod


def test_on_an_english_windows_arabic_in_the_sql_and_in_parameters_works(monkeypatch):
    db = _db(monkeypatch, 1252)
    assert db.ansi_codec() == "cp1252" and db.wide_text()
    db.execute("INSERT INTO po (name, status) VALUES (?, 'تم السداد')", ("مورد الأسمنت",))
    row = db.query("SELECT name FROM po WHERE status <> 'استلام جزئي'")[0]            # an Arabic literal inside the SQL: the exact case that crashed
    assert row["name"] == "مورد الأسمنت"                                                # and the parameter is NOT turned into question marks


def test_an_arabic_pc_keeps_the_narrow_behaviour_that_is_proven(monkeypatch):
    db = _db(monkeypatch, 1256)
    assert db.ansi_codec() == "cp1256" and not db.wide_text()
    db.execute("INSERT INTO po (name, status) VALUES (?, 'x')", ("مورد",))
    assert db.query("SELECT name FROM po")[0]["name"] == "مورد"


def test_arabic_indic_digits_are_still_normalised_in_wide_mode(monkeypatch):
    db = _db(monkeypatch, 1252)
    db.execute("INSERT INTO po (name, status) VALUES (?, 'x')", ("أمر ١٢٣",))
    assert db.query("SELECT name FROM po")[0]["name"] == "أمر 123"

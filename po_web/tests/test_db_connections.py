# -*- coding: utf-8 -*-
"""The REAL db.py (not the test stand-in) with a pretend pyodbc that counts how many times a connection is opened.
Speed: a page must open ONE connection for all its reads; a write must never leave a stale reader behind (audit C-09)."""
import importlib.util
import os
import sqlite3
import sys
import types

import pytest
from flask import Flask

import conftest

OPENS = [0]
_SHARED = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)


class _Cur:
    def __init__(self):
        self._c = _SHARED.cursor()

    def execute(self, sql, params=()):
        self._c.execute(sql, params)
        return self

    def setinputsizes(self, sizes):
        pass

    @property
    def description(self):
        return self._c.description

    def fetchall(self):
        return self._c.fetchall()

    def fetchone(self):
        return self._c.fetchone()

    def close(self):
        pass


class _Conn:
    def __init__(self):
        OPENS[0] += 1
        self.closed = False

    def setdecoding(self, *a, **k):
        pass

    def setencoding(self, *a, **k):
        pass

    def cursor(self):
        return _Cur()

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = True


@pytest.fixture()
def real_db(monkeypatch):
    stub = types.ModuleType("pyodbc")
    stub.SQL_CHAR, stub.SQL_WCHAR, stub.SQL_LONGVARCHAR = 1, -8, -1
    stub.Error = type("Error", (Exception,), {})
    stub.connect = lambda *a, **k: _Conn()
    monkeypatch.setitem(sys.modules, "pyodbc", stub)
    spec = importlib.util.spec_from_file_location("real_db_under_test", os.path.join(conftest.ROOT, "db.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _SHARED.execute("DROP TABLE IF EXISTS t")
    _SHARED.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    _SHARED.execute("INSERT INTO t (v) VALUES ('a')")
    OPENS[0] = 0
    return mod


def test_outside_a_request_every_call_opens_its_own_connection(real_db):
    for _ in range(3):
        real_db.query("SELECT * FROM t")
    assert OPENS[0] == 3


def test_all_the_reads_of_one_request_share_one_connection(real_db):
    app = Flask("t")
    with app.test_request_context("/"):
        for _ in range(20):
            assert real_db.query("SELECT v FROM t")[0]["v"] == "a"
        assert OPENS[0] == 1
    assert OPENS[0] == 1


def test_a_write_closes_the_reader_so_the_next_read_is_fresh(real_db):
    app = Flask("t")
    with app.test_request_context("/"):
        real_db.query("SELECT * FROM t")
        first = real_db._request_globals()._po_read_conn
        real_db.execute("INSERT INTO t (v) VALUES ('b')")
        assert first.closed
        assert len(real_db.query("SELECT * FROM t")) == 2           # sees the new row
        with real_db.transaction() as tx:
            tx.execute("INSERT INTO t (v) VALUES ('c')")
        assert len(real_db.query("SELECT * FROM t")) == 3


def test_a_failing_query_does_not_leave_a_broken_connection_behind(real_db):
    app = Flask("t")
    with app.test_request_context("/"):
        real_db.query("SELECT * FROM t")
        with pytest.raises(sqlite3.OperationalError):
            real_db.query("SELECT * FROM no_such_table")
        assert real_db._request_globals().get("_po_read_conn") is None
        assert len(real_db.query("SELECT * FROM t")) == 1


def test_the_connection_is_closed_when_the_request_ends(real_db):
    app = Flask("t")
    app.teardown_request(lambda exc: real_db.close_request_conn())
    c = app.test_client()
    seen = []

    @app.route("/")
    def index():
        real_db.query("SELECT * FROM t")
        seen.append(real_db._request_globals()._po_read_conn)
        return "ok"
    c.get("/")
    assert seen[0].closed

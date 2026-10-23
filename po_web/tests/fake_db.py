# -*- coding: utf-8 -*-
"""
fake_db.py - a stand-in for db.py used ONLY by the automatic tests.

The real db.py talks to Microsoft Access. Access is not available where the tests run, so this module offers the same
functions (query / scalar / execute / execute_insert / transaction ...) on top of SQLite. It translates the few Access-only
spellings the application uses (AUTOINCREMENT PRIMARY KEY, Now(), YESNO, MEMO, CURRENCY, @@IDENTITY).

It proves the application's own logic (money rules, statuses, guards). It does NOT prove Access behaviour: the final
check on a COPY of the real database stays with the person who owns the machine.
"""
import contextlib
import re
import sqlite3
import types
from datetime import date, datetime

DB_PATH = ":memory:"
_conn = None
_last_id = [None]
CALLS = [0]                       # every call is one Access connection in the real db.py: the number of calls per page is the speed budget


def _adapt_dt(v):
    return v.strftime("%Y-%m-%d %H:%M:%S")


sqlite3.register_adapter(datetime, _adapt_dt)
sqlite3.register_adapter(date, lambda v: v.strftime("%Y-%m-%d"))


def reset():
    """A fresh empty database."""
    global _conn
    if _conn is not None:
        _conn.close()
    _conn = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
    _conn.row_factory = sqlite3.Row


def _tr(sql):
    top = re.match(r"\s*SELECT\s+TOP\s+(\d+)\s", sql, flags=re.I)
    if top:                                                  # Access «SELECT TOP n ...» -> SQLite «... LIMIT n»
        sql = re.sub(r"SELECT\s+TOP\s+\d+\s", "SELECT ", sql, count=1, flags=re.I).rstrip() + " LIMIT " + top.group(1)
    sql = re.sub(r"\bAUTOINCREMENT\s+PRIMARY\s+KEY\b", "INTEGER PRIMARY KEY AUTOINCREMENT", sql, flags=re.I)
    sql = re.sub(r"DATEADD\('d',\s*(\d+),\s*Now\(\)\)", r"datetime('now','localtime','+\1 days')", sql, flags=re.I)
    sql = re.sub(r"\bNow\(\)", "datetime('now','localtime')", sql, flags=re.I)
    sql = re.sub(r"\bYESNO\b", "INTEGER", sql, flags=re.I)
    sql = re.sub(r"\bMEMO\b", "TEXT", sql, flags=re.I)
    sql = re.sub(r"\bCURRENCY\b", "REAL", sql, flags=re.I)
    sql = re.sub(r"\bDATETIME\b(?!\()", "TEXT", sql, flags=re.I)
    return sql


_DT_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d")


def _row(cols, row):
    out = {}
    for c, v in zip(cols, row):
        out[c] = v
    return out


def _run(cur, sql, params=()):
    CALLS[0] += 1
    params = tuple(1 if p is True else 0 if p is False else p for p in (params or ()))
    return cur.execute(_tr(sql), params)


def _convert_dates(rows):
    """TEXT dates come back as datetime like Access does (columns named *At / *Date / Date)."""
    for r in rows:
        for k, v in list(r.items()):
            if isinstance(v, str) and (k.endswith("At") or k.endswith("Date") or k in ("Date", "DueDate")) and len(v) >= 10:
                for f in _DT_FORMATS:
                    try:
                        r[k] = datetime.strptime(v[:19] if len(v) >= 19 else v[:10], f)
                        break
                    except ValueError:
                        continue
    return rows


def query(sql, params=()):
    cur = _conn.cursor()
    _run(cur, sql, params)
    cols = [c[0] for c in cur.description]
    return _convert_dates([_row(cols, r) for r in cur.fetchall()])


def scalar(sql, params=(), default=None):
    rows = query(sql, params)
    if not rows:
        return default
    v = list(rows[0].values())[0]
    return default if v is None else v


def execute(sql, params=()):
    _run(_conn.cursor(), sql, params)


def execute_insert(sql, params=()):
    cur = _conn.cursor()
    _run(cur, sql, params)
    return cur.lastrowid


def table_exists(name):
    return bool(_conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND lower(name)=lower(?)", (name,)).fetchone())


class _Cur:
    def __init__(self):
        self._c = _conn.cursor()

    def execute(self, sql, params=()):
        if sql.strip().upper() == "SELECT @@IDENTITY":
            self._c = _conn.execute("SELECT last_insert_rowid()")
            return self._c
        _run(self._c, sql, params)
        return self._c

    def fetchone(self):
        return self._c.fetchone()

    def fetchall(self):
        return self._c.fetchall()

    @property
    def description(self):
        return self._c.description

    def columns(self, table=None):
        return [types.SimpleNamespace(column_name=r[1], column_size=255, type_name="VARCHAR")
                for r in _conn.execute("PRAGMA table_info([%s])" % table).fetchall()]

    def tables(self, tableType=None):
        return [types.SimpleNamespace(table_name=r[0]) for r in _conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]


class _Conn:
    def cursor(self):
        return _Cur()

    def close(self):
        pass

    def commit(self):
        pass

    def rollback(self):
        pass


def get_conn(autocommit=True):
    return _Conn()


class Transaction:
    def query(self, sql, params=()):
        return query(sql, params)

    def execute(self, sql, params=()):
        execute(sql, params)

    def execute_insert(self, sql, params=()):
        return execute_insert(sql, params)


@contextlib.contextmanager
def transaction():
    _conn.execute("BEGIN")
    try:
        yield Transaction()
        _conn.execute("COMMIT")
    except BaseException:
        _conn.execute("ROLLBACK")
        raise


def close_request_conn():
    pass


def ansi_codec():
    return "utf-8"


def fit_text(value, codec=None):
    return value


def column_limits(table):
    return {}


def too_long(table, values):
    return []

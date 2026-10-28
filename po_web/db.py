# -*- coding: utf-8 -*-
"""
db.py - the ONLY module that opens a connection to PurchaseSystem.accdb.
Every route talks to the database through the helpers here, so the
connection string, encoding fix, and quoting rules live in exactly one
place (same principle as modDB.bas on the Excel side).

One important architectural point: unlike the old Excel/VBA tool, this
database is now only ever touched by THIS one server process. Multiple
office PCs no longer each open their own connection to the .accdb file -
they all talk over HTTP to this single server, which serializes access
internally. That is what removes the file-locking problem the Excel
version had.
"""
import contextlib
import decimal
import pyodbc

import config

# Full path of the .accdb file - comes from config.ini / the PO_DB_PATH environment variable.
DB_PATH = config.DB_PATH


_codec_cache = []


def _get_acp():
    """The Windows ANSI code page of THIS PC (1256 = Arabic, 1252 = Western, 65001 = UTF-8 option enabled)."""
    import ctypes
    return ctypes.windll.kernel32.GetACP()


def ansi_codec():
    """The text encoding the Access ODBC driver uses for narrow (char) data on this PC.

    The driver converts Access's Unicode text to / from the PC's ANSI code page. A fixed 'utf-8' (the old
    behaviour) only works on a PC where that code page happens to be UTF-8; on an ordinary Arabic Windows
    (code page 1256) reading fails with 'invalid continuation byte' and WRITING would store Arabic garbled.
    So the codec is taken from the PC itself (or from config.ini -> ansi_encoding)."""
    if _codec_cache:
        return _codec_cache[0]
    codec = config.ANSI_ENCODING
    if not codec or codec == "auto":
        try:
            acp = int(_get_acp())
            codec = "utf-8" if acp == 65001 else "cp%d" % acp
        except Exception:                          # noqa - not Windows / no ctypes: keep the previous behaviour
            codec = "utf-8"
    import codecs
    try:
        codecs.lookup(codec)
    except LookupError:
        codec = "utf-8"
    _codec_cache.append(codec)
    return codec


def wide_text():
    """True on a PC whose ANSI code page cannot hold Arabic (e.g. 1252, an English Windows): there the text we send travels as UTF-16
    («wide»), which every PC can carry, instead of the narrow code page that would crash on the first Arabic letter or turn it into «?»."""
    return ansi_codec() not in ("utf-8", "cp1256")


def get_conn(autocommit=True):
    """Opens a fresh connection. Access/Jet has no real connection pooling,
    so callers open one per request and close it when done."""
    conn_str = (
        r"DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};"
        rf"DBQ={DB_PATH};"
    )
    conn = pyodbc.connect(conn_str, autocommit=autocommit)
    codec = ansi_codec()
    conn.setdecoding(pyodbc.SQL_CHAR, encoding=codec)        # narrow text coming FROM the driver
    conn.setdecoding(pyodbc.SQL_WCHAR, encoding="utf-16le")  # wide text is always UTF-16 little-endian
    if wide_text():
        conn.setencoding(encoding="utf-16le", ctype=pyodbc.SQL_WCHAR)      # the SQL text and the parameters we send TO the driver (any code page)
    else:
        conn.setencoding(encoding=codec)                     # the PC's code page can carry Arabic: keep the narrow behaviour that is proven
    return conn


# ---- text that the PC's ANSI code page cannot hold ---------------------------------------------------------
# On a PC whose Windows code page is 1256 (ordinary Arabic Windows) the driver can only exchange characters that
# exist in that code page. Everything normal is there (all Arabic letters, tashkeel, tatweel, Persian letters,
# Latin digits); these are not, and used to crash the save with a 'charmap' error:
#   - Arabic-Indic digits  \u0660-\u0669 (a common copy-paste from PDFs and Word)   -> converted to 0-9
#   - Persian digits       \u06F0-\u06F9                                          -> converted to 0-9
#   - the Arabic percent sign and the riyal symbols                                 -> % / the word ريال
#   - invisible direction marks that PDFs and Word leave in copied text             -> removed
# Anything else outside the code page becomes '?' instead of an error. On a PC with the UTF-8 option nothing is touched.
from textfit import fit_text as _fit, fit_wide as _fit_wide


def fit_text(value, codec=None):
    """Makes a string safe to send to the driver on this PC (see textfit.py)."""
    if codec is None and wide_text():
        return _fit_wide(value)                          # nothing is lost in UTF-16: only the invisible marks / Arabic-Indic digits are normalised
    return _fit(value, codec or ansi_codec())


def _fit_params(params):
    if not params:
        return params
    codec = ansi_codec()
    return tuple(fit_text(p) for p in params)


# ---- long text -----------------------------------------------------------------------------------------------------
# The Access driver refuses a text PARAMETER whose size is over 255 ("HY104 Invalid precision value"). The size is counted in
# the bytes of the encoding used to send it: on a PC whose Windows code page is UTF-8 every Arabic letter is two bytes, so a
# 100-letter archive path is already 200+ bytes and a file path of ~130 letters is refused although the column holds 255
# letters. Such a parameter is therefore declared as long text (SQL_LONGVARCHAR); the driver then converts it like any other.
_LONG_BYTES = 250


def _long_sizes(params):
    """setinputsizes() list that declares the over-long text parameters as long text, or None when there is none."""
    if not params:
        return None
    codec = ansi_codec()
    sizes, found = [], False
    for p in params:
        if isinstance(p, str):
            wide = wide_text()
            n = len(p) if wide else len(p.encode(codec, errors="replace"))      # UTF-16 text is sized in characters, narrow text in bytes
            if n > _LONG_BYTES:
                sizes.append((getattr(pyodbc, "SQL_WLONGVARCHAR", pyodbc.SQL_LONGVARCHAR) if wide else pyodbc.SQL_LONGVARCHAR, n, 0))
                found = True
                continue
        sizes.append(None)
    return sizes if found else None


def _run(cur, sql, params=()):
    """cursor.execute(): the one place every statement goes through (text fitting + long-text declaration)."""
    params = _fit_params(params)
    sizes = _long_sizes(params)
    if sizes is None:
        return cur.execute(sql, params)
    try:
        cur.setinputsizes(sizes)
    except Exception:                                  # noqa - an old driver build: send it as it is
        return cur.execute(sql, params)
    try:
        return cur.execute(sql, params)
    finally:
        try:
            cur.setinputsizes(None)
        except Exception:                              # noqa
            pass


def _clean(v):
    """pyodbc returns Access CURRENCY columns as decimal.Decimal, while DOUBLE
    columns come back as plain float. Mixing the two in arithmetic (e.g.
    Qty * UnitPrice) raises TypeError, so every value is normalised to a
    plain float right here, once, instead of at every call site."""
    return float(v) if isinstance(v, decimal.Decimal) else v


# ---- ONE connection per web request for reading ---------------------------------------------------------------------
# Opening a connection to an Access file is the expensive part of every query (tens to hundreds of milliseconds, much more on a slow disk or with an
# anti-virus watching the file). A page asks the database 6-25 small questions; opening a new connection for each made every page take seconds.
# Inside a web request all the READS now share one connection (opened at the first question, closed when the request ends). Any WRITE closes it first
# and after it is done, so a later read in the same request opens a fresh connection and always sees what was just saved (Access/Jet may serve
# a stale cached page to an older connection). Outside a request (tools, migrations, tests) nothing changes: one connection per call.
def _request_globals():
    try:
        from flask import g, has_request_context
    except ImportError:                                  # pragma: no cover - the tools run without Flask installed
        return None
    return g if has_request_context() else None


def _read_conn():
    """(connection, owned). `owned` = the caller must close it (outside a web request)."""
    g_ = _request_globals()
    if g_ is None:
        return get_conn(), True
    conn = g_.get("_po_read_conn")
    if conn is None:
        conn = get_conn()
        g_._po_read_conn = conn
    return conn, False


def close_request_conn():
    """Closes the request's shared read connection (called after every write and when the request ends)."""
    g_ = _request_globals()
    if g_ is None:
        return
    conn = g_.pop("_po_read_conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:                                # noqa - closing must never raise
            pass


def query(sql, params=()):
    """SELECT helper: returns a list of dicts (column name -> value)."""
    conn, owned = _read_conn()
    try:
        cur = conn.cursor()
        _run(cur, sql, params)
        cols = [c[0] for c in cur.description]
        rows = [{c: _clean(v) for c, v in zip(cols, row)} for row in cur.fetchall()]
        cur.close()
        return rows
    except BaseException:
        if not owned:
            close_request_conn()                         # never keep a connection that failed
        raise
    finally:
        if owned:
            conn.close()


def scalar(sql, params=(), default=None):
    rows = query(sql, params)
    if not rows:
        return default
    v = list(rows[0].values())[0]
    return default if v is None else v


def execute(sql, params=()):
    """INSERT / UPDATE / DELETE helper."""
    close_request_conn()
    conn = get_conn()
    try:
        cur = conn.cursor()
        _run(cur, sql, params)
    finally:
        conn.close()


def execute_insert(sql, params=()):
    """INSERT helper that also returns the new AutoNumber ID. @@IDENTITY is
    scoped to the connection that did the insert, so - unlike execute() -
    this keeps one connection open for both statements instead of opening
    a fresh one (which would just see NULL)."""
    close_request_conn()
    conn = get_conn()
    try:
        cur = conn.cursor()
        _run(cur, sql, params)
        cur.execute("SELECT @@IDENTITY")
        return cur.fetchone()[0]
    finally:
        conn.close()


def table_exists(name):
    conn = get_conn()
    try:
        cur = conn.cursor()
        for row in cur.tables(tableType="TABLE"):
            if row.table_name.lower() == name.lower():
                return True
        return False
    finally:
        conn.close()


# ---- all-or-nothing writes ---------------------------------------------------------------------------------------
class Transaction:
    """One connection for several statements. Used where a save is more than one INSERT/UPDATE (an order and its
    items, a contract and its first site ...): if ANY statement fails nothing is kept, so a half-saved record - an
    order whose items were deleted but not re-inserted - can never be left behind."""

    def __init__(self, conn):
        self._conn = conn

    def query(self, sql, params=()):
        cur = self._conn.cursor()
        _run(cur, sql, params)
        cols = [c[0] for c in cur.description]
        return [{c: _clean(v) for c, v in zip(cols, row)} for row in cur.fetchall()]

    def execute(self, sql, params=()):
        _run(self._conn.cursor(), sql, params)

    def execute_insert(self, sql, params=()):
        cur = self._conn.cursor()
        _run(cur, sql, params)
        cur.execute("SELECT @@IDENTITY")
        return int(cur.fetchone()[0])


@contextlib.contextmanager
def transaction():
    """with db.transaction() as tx:  tx.execute(...); tx.execute_insert(...)  - committed together, or rolled back."""
    real = True
    close_request_conn()
    try:
        conn = get_conn(autocommit=False)
    except pyodbc.Error:                               # a driver without transactions: statements run one by one, as before
        conn, real = get_conn(), False
    try:
        yield Transaction(conn)
        if real:
            conn.commit()
    except BaseException:
        if real:
            try:
                conn.rollback()
            except pyodbc.Error:
                pass
        raise
    finally:
        conn.close()
        close_request_conn()                             # the next read in this request must see what the transaction saved


# ---- text longer than its column ---------------------------------------------------------------------------------
_LIMITS = {}


def column_limits(table):
    """{column: maximum characters} of the short-text columns of a table, read once from the database itself
    (long-text / memo columns have no practical limit and are left out). {} when it cannot be read."""
    key = table.lower()
    if key not in _LIMITS:
        out = {}
        try:
            conn = get_conn()
            try:
                for c in conn.cursor().columns(table=table):
                    if c.column_size and 0 < c.column_size <= 255 and "CHAR" in (c.type_name or "").upper():
                        out[c.column_name] = c.column_size
            finally:
                conn.close()
        except Exception:                              # noqa - no limits known: the database itself will still refuse an overflow
            out = {}
        _LIMITS[key] = out
    return _LIMITS[key]


def too_long(table, values):
    """values {column: text} -> [(column, limit, length)] for every text that would not fit its column."""
    limits = column_limits(table)
    return [(col, limits[col], len(str(v))) for col, v in values.items() if v and col in limits and len(str(v)) > limits[col]]

# -*- coding: utf-8 -*-
r"""
test_long_text.py - proves, on a THROW-AWAY COPY of the database, that a long Arabic text can be saved on this PC.

    python tools\test_long_text.py

The real database is only copied (never opened for writing). A temporary table is created in the copy, several ways of
sending a long text are tried, and the copy is deleted at the end. Send the whole output.
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pyodbc  # noqa: E402

import config  # noqa: E402
import db  # noqa: E402

TEXT = ("ملف المستخلص " * 20)[:254]                 # 254 letters: fits the 255-letter column, far over 255 bytes in UTF-8


def main():
    src = config.DB_PATH
    tmp = tempfile.mkdtemp(prefix="po_longtext_")
    copy = os.path.join(tmp, "copy.accdb")
    shutil.copy2(src, copy)
    db.DB_PATH = copy
    print("ترميز هذا الجهاز: %s | طول النص المُجرَّب: %d حرفًا = %d بايت" % (db.ansi_codec(), len(TEXT), len(TEXT.encode(db.ansi_codec(), errors="replace"))))
    print("النسخة المؤقتة:", copy)
    ok_any = False
    try:
        conn = db.get_conn()
        conn.cursor().execute("CREATE TABLE [ZZ_LongText] ([N] INTEGER, [T] TEXT(255))")
        conn.close()

        def attempt(name, fn):
            nonlocal ok_any
            try:
                fn()
                got = db.query("SELECT [T] FROM [ZZ_LongText] WHERE [N] = ?", (attempts[name],))
                same = bool(got) and got[0]["T"] == TEXT
                print("  [%s] %s - %s" % ("OK" if same else "!!", name, "حُفظ وقُرئ مطابقًا" if same else "حُفظ لكن القراءة مختلفة: %r" % (got[0]["T"][:40] if got else None)))
                ok_any = ok_any or same
            except Exception as e:                    # noqa
                print("  [!!] %s - فشل: %s" % (name, " ".join(str(e).split())[:140]))

        attempts = {}
        n = [0]

        def nxt(name):
            n[0] += 1
            attempts[name] = n[0]
            return n[0]

        def plain():
            c = db.get_conn()
            try:
                c.cursor().execute("INSERT INTO [ZZ_LongText] ([N], [T]) VALUES (?, ?)", (nxt("1 الطريقة القديمة"), TEXT))
            finally:
                c.close()
        attempt("1 الطريقة القديمة", plain)

        def fixed():
            db.execute("INSERT INTO [ZZ_LongText] ([N], [T]) VALUES (?, ?)", (nxt("2 الإصلاح في db.py"), TEXT))
        attempt("2 الإصلاح في db.py", fixed)

        def wlong():
            c = db.get_conn()
            try:
                cur = c.cursor()
                cur.setinputsizes([None, (pyodbc.SQL_WLONGVARCHAR, len(TEXT), 0)])
                cur.execute("INSERT INTO [ZZ_LongText] ([N], [T]) VALUES (?, ?)", (nxt("3 نص طويل عريض"), TEXT))
            finally:
                c.close()
        attempt("3 نص طويل عريض", wlong)

        def wide():
            c = db.get_conn()
            try:
                c.setencoding("utf-16le", ctype=pyodbc.SQL_WCHAR)
                c.cursor().execute("INSERT INTO [ZZ_LongText] ([N], [T]) VALUES (?, ?)", (nxt("4 ترميز عريض UTF-16"), TEXT))
            finally:
                c.close()
        attempt("4 ترميز عريض UTF-16", wide)
    finally:
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:                             # noqa
            pass
    print("\nالنتيجة:", "الإصلاح (الطريقة 2) يعمل على هذا الجهاز." if ok_any else "لا طريقة نجحت - ابعت هذا الناتج كاملًا.")
    print("القاعدة الأصلية لم تُعدَّل. حُذفت النسخة المؤقتة.")


if __name__ == "__main__":
    main()

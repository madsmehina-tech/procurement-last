r"""
python tools\diagnose_text.py                  - READ-ONLY: shows how this PC / Access driver hands over Arabic text.
python tools\diagnose_text.py --write-test     - also writes test rows into a scratch table, reads them back and deletes
                                                 the table (use it on the COPY of the database only).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pyodbc

import config
import db

# (label, text written, text we expect to read back on a PC whose code page is 1256)
SAMPLES = [
    ("حروف عربية + تشكيل + أرقام لاتينية", "شركة الهضب للتجارة والمقاولات - مُحَمَّد - PO-2026-001", "شركة الهضب للتجارة والمقاولات - مُحَمَّد - PO-2026-001"),
    ("أرقام هندية ونسبة عربية",           "مشروع ١٢٣ بنسبة ١٥٪",                                      "مشروع 123 بنسبة 15%"),
    ("علامات اتجاه مخفية من PDF",          "\u202bالنرجس\u202c ٤٥٦",                                    "النرجس 456"),
    ("رمز الريال",                         "المبلغ ١٠٠ ﷼",                                              "المبلغ 100 ريال"),
]


def show_texts(sql, label):
    try:
        rows = db.query(sql)
    except UnicodeDecodeError as e:
        print("  %-24s ✘ فشل: %s" % (label, e))
        return None
    vals = [str(list(r.values())[0]) for r in rows if list(r.values())[0]]
    print("  %-24s ✔ %d سجل" % (label, len(vals)))
    for v in vals[:3]:
        print("      ", v, ("   ⚠ فيه علامة ؟" if "?" in v else ""))
    return len(vals)


def main():
    print("== هذا الجهاز")
    try:
        print("ترميز ANSI في ويندوز (code page):", db._get_acp())
    except Exception as e:                                   # noqa
        print("تعذّر قراءة ترميز ويندوز:", e)
    print("الترميز الذي سيستخدمه النظام:", db.ansi_codec(), "(الإعداد في config.ini: %s)" % config.ANSI_ENCODING)
    print("pyodbc:", pyodbc.version)
    print("محركات Access:", [d for d in pyodbc.drivers() if "Access" in d])
    print("القاعدة:", db.DB_PATH)

    print("\n== نوع عمود النص كما يصفه المحرك")
    conn = pyodbc.connect(r"DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=%s;" % db.DB_PATH, autocommit=True)
    try:
        for c in conn.cursor().columns(table="PurchaseOrders"):
            if c.column_name == "SupplierName":
                print("PurchaseOrders.SupplierName: النوع", c.data_type, "(12 = نص ضيق ANSI ، -9 = نص واسع Unicode)")
    except Exception as e:                                   # noqa
        print("تعذّر:", e)
    finally:
        conn.close()

    print("\n== قراءة بيانات حقيقية بإعدادات النظام الحالية (الجداول التي فشلت سابقًا)")
    n1 = show_texts("SELECT TOP 5 [SupplierName] FROM [PurchaseOrders] WHERE [SupplierName] IS NOT NULL", "أوامر الشراء (المورد)")
    n2 = show_texts("SELECT TOP 5 [Project] FROM [PurchaseOrders] WHERE [Project] IS NOT NULL", "أوامر الشراء (المشروع)")
    n3 = show_texts("SELECT TOP 5 [SiteName] FROM [Contracts] WHERE [SiteName] IS NOT NULL", "العقود (الموقع)")
    if not any([n1, n2, n3]):
        print("  لا توجد بيانات عربية في هذه الجداول لاختبارها.")

    if "--write-test" in sys.argv:
        print("\n== اختبار كتابة (جدول مؤقت يُحذف في النهاية)")
        passed = 0
        try:
            if db.table_exists("_EncTest"):
                db.execute("DROP TABLE [_EncTest]")
            db.execute("CREATE TABLE [_EncTest] ([Id] AUTOINCREMENT PRIMARY KEY, [T] TEXT(120), [M] MEMO)")
            for label, text, expect in SAMPLES:
                try:
                    db.execute("DELETE FROM [_EncTest]")
                    db.execute("INSERT INTO [_EncTest] ([T]) VALUES (?)", (text,))
                    back = db.query("SELECT [T] FROM [_EncTest]")[0]["T"]
                except Exception as e:                       # noqa
                    print("  ✘ %-36s خطأ: %s" % (label, e))
                    continue
                good = back == expect or (db.ansi_codec() == "utf-8" and back == text)
                passed += good
                print("  %s %-36s كُتب: %s | قُرئ: %s" % ("✔" if good else "✘", label, text, back))
        finally:
            try:
                db.execute("DROP TABLE [_EncTest]")
            except Exception:                                # noqa
                pass
        try:                                                 # the order revision history stores a long JSON text
            long_text = ("شركة الهضب للتجارة والمقاولات - صنف رقم 1 - " * 90)[:4000]
            db.execute("DELETE FROM [_EncTest]")
            db.execute("INSERT INTO [_EncTest] ([M]) VALUES (?)", (long_text,))
            good = db.query("SELECT [M] FROM [_EncTest]")[0]["M"] == long_text
            passed += good
            print("  %s %-36s %d حرفًا في حقل نص طويل" % ("✔" if good else "✘", "نص طويل (سجل نسخ الأوامر)", len(long_text)))
        except Exception as e:                               # noqa
            print("  ✘ نص طويل (سجل نسخ الأوامر) خطأ:", e)
        total = len(SAMPLES) + 1
        print("النتيجة: %d من %d اختبارات ناجحة" % (passed, total))
    return 0


if __name__ == "__main__":
    sys.exit(main())

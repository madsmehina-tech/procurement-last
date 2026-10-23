# -*- coding: utf-8 -*-
"""
m026_unique_numbers - the database itself refuses a duplicate purchase-order number, payment-cycle number or number-series row.

Until now the only protection was the application's write lock («read the last number, add one, insert»). That is enough while ONE server process
runs, but a second process (a forgotten console window, a tool run at the wrong moment) could create two orders with the same number. A unique index
makes the database refuse the second one whatever happens.

Safe by design: it first looks for duplicates ALREADY in the data. If it finds any it skips that index, prints exactly what to fix, and carries on -
it never fails the migration and never changes your data. Run it again after fixing the duplicates and the index is created.
(The contract number is deliberately not indexed: a contract may be saved without a number, and several «empty» numbers are legal.)
"""
import db

INDEXES = [
    ("PurchaseOrders", "ux_po_number", ["PONumber"], "رقم أمر الشراء"),
    ("PaymentCycles", "ux_cycle_no", ["CycleNo"], "رقم دورة الصرف"),
    ("NumberSeries", "ux_number_series", ["SeriesKey", "SeriesYear"], "سلسلة ترقيم العقود"),
]


def _duplicates(table, cols):
    cl = ", ".join("[%s]" % c for c in cols)
    return db.query("SELECT %s, COUNT(*) AS N FROM [%s] GROUP BY %s HAVING COUNT(*) > 1" % (cl, table, cl))


def main():
    for table, name, cols, label in INDEXES:
        if not db.table_exists(table):
            print("- %s: table not found, skipped" % table)
            continue
        dups = _duplicates(table, cols)
        if dups:
            shown = "؛ ".join(" / ".join(str(d[c]) for c in cols) + " (×%d)" % d["N"] for d in dups[:10])
            print("!! %s: يوجد تكرار في %s فلم يُنشأ الفهرس: %s" % (table, label, shown))
            print("   صحّح التكرار ثم أعد تشغيل:  python migrate.py")
            continue
        try:
            db.execute("CREATE UNIQUE INDEX [%s] ON [%s] (%s)" % (name, table, ", ".join("[%s]" % c for c in cols)))
            print("- %s.%s: unique index created" % (table, ", ".join(cols)))
        except Exception as e:                           # noqa - an index that already exists is fine; any other problem is reported, never fatal
            msg = " ".join(str(e).split())[:140]
            print("- %s.%s: %s" % (table, ", ".join(cols), "already exists" if "already exists" in msg.lower() or "موجود" in msg else "skipped (%s)" % msg))

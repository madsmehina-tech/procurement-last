# -*- coding: utf-8 -*-
r"""
python tools\wipe_test_data.py                     - shows what it WOULD remove (changes nothing)
python tools\wipe_test_data.py --apply            - removes it, after you type the database file name to confirm

Clears the DATA that was typed while testing - purchase orders, quote comparisons, contracts, certificates, payments, payment cycles,
account-statement movements and the archive records - and restarts the numbering (PO-2026-001, cycle 1, ...).

KEPT: users, roles and permissions, company data, contract templates, projects and sites, suppliers, contractors, items names, the audit log.
FILES: archive files are NOT deleted: they are moved into the folder  _wiped_<date>  inside the archive, so nothing is lost.
SAFETY: a full backup of the database is made first; the confirmation is the database file name, so a wrong copy is not wiped by mistake.
"""
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import archive
import audit
import backup
import config
import db

# children first, so nothing points at a row that is already gone
WIPE = ["OrderRevisions", "OrderItems", "PurchaseOrders", "ComparisonItems", "QuoteLines", "Quotes", "Comparisons",
        "Payments", "CycleDecisions", "CycleLog", "PaymentCycles", "LedgerEntries", "Certificates",
        "ContractSites", "ContractLines", "ContractTranches", "ContractData", "ContractHistory", "Contracts",
        "Documents", "NumberSeries"]


def plan():
    """[(table, row count)] for the tables that exist."""
    out = []
    for t in WIPE:
        if db.table_exists(t):
            out.append((t, int(db.scalar("SELECT COUNT(*) FROM [%s]" % t, default=0) or 0)))
    return out


def move_archive_files(stamp):
    """Moves every top-level folder / file of the archive into _wiped_<stamp> (nothing is deleted). Returns the new folder or None."""
    root = os.path.normpath(config.ARCHIVE_ROOT)
    if not os.path.isdir(root):
        return None
    keep_prefix = "_wiped_"
    items = [n for n in os.listdir(root) if not n.startswith(keep_prefix)]
    if not items:
        return None
    dest = os.path.join(root, keep_prefix + stamp)
    os.makedirs(dest, exist_ok=True)
    for n in items:
        shutil.move(os.path.join(root, n), os.path.join(dest, n))
    return dest


def apply(user="wipe_test_data"):
    """The whole job. Raises (and changes nothing) if the backup cannot be made."""
    copy = backup.backup_database("before_wipe")
    if not copy:
        raise RuntimeError("لم يُعثر على ملف قاعدة البيانات لأخذ نسخة احتياطية.")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    counts = plan()
    with db.transaction() as tx:
        for t, _n in counts:
            tx.execute("DELETE FROM [%s]" % t)
    moved = move_archive_files(stamp)
    audit.log("wipe_test_data", "", "", "؛ ".join("%s=%d" % c for c in counts if c[1]), user={"Username": user})
    return {"backup": copy, "counts": counts, "files_moved_to": moved}


def main(argv):
    counts = plan()
    total = sum(n for _t, n in counts)
    print("قاعدة البيانات:", config.DB_PATH)
    print("البيئة:", config.ENVIRONMENT)
    for t, n in counts:
        print("  %-20s %6d" % (t, n))
    print("المجموع: %d سجلاً" % total)
    if "--apply" not in argv:
        print("\nهذا عرض فقط ولم يتغيّر شيء. للتنفيذ أضف --apply")
        return 0
    expected = os.path.basename(config.DB_PATH)
    typed = input("\nللتأكيد اكتب اسم ملف قاعدة البيانات (%s) ثم Enter: " % expected).strip()
    if typed.lower() != expected.lower():
        print("الاسم غير مطابق. لم يتغيّر شيء.")
        return 1
    r = apply()
    print("\nتمّ. النسخة الاحتياطية:", r["backup"])
    print("ملفات الأرشيف نُقلت إلى:", r["files_moved_to"] or "(لا توجد ملفات)")
    print("أعد تشغيل السيرفر.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

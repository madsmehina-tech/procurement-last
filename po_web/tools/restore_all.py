# -*- coding: utf-8 -*-
r"""python tools\restore_all.py --set D:\PO_BackupSet --db-out D:\PO_RESTORE\restored.accdb --archive-out D:\PO_RESTORE\archive [--legacy-out D:\PO_RESTORE\legacy] [--verify full|sample|none]
Rebuilds a database file and an archive folder from a backup set into NEW places. It REFUSES to write over (or inside, or around) the production
database / archive. After it, point a test copy of the system at the restored files to look at them."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import storage_tools as st


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def main():
    s, d, a = arg("--set"), arg("--db-out"), arg("--archive-out")
    if not (s and d and a):
        print(__doc__)
        return 2
    try:
        r = st.restore_set(s, d, a, verify=arg("--verify", "sample"), progress=lambda n: print("  ... %d ملف" % n, flush=True),
                           link_reader=st.read_document_paths, allow_incomplete="--allow-incomplete" in sys.argv, legacy_out=arg("--legacy-out"))
    except (st.ProductionGuard, OSError) as e:
        print("[!!] توقّف:", e)
        return 1
    print("القاعدة مطابقة للبصمة: %s" % ("نعم" if r["db_ok"] else "لا"))
    print("الملفات: استُرجع %d من %d · مفقود في النسخة %d · فُحصت بصمة %d ملفاً (اختلاف %d) · %s ثانية" % (r["restored"], r["archive_expected"], r["missing_in_backup_count"], r["hash_checked"], r["hash_mismatch_count"], r["seconds"]))
    if r.get("legacy_expected"):
        print("المرفقات القديمة: استُرجع %d من %d إلى %s" % (r["legacy_restored"], r["legacy_expected"], arg("--legacy-out")))
    print("روابط المستندات: " + ("فُحصت %d رابطاً، المفقود %d" % (r["links_paths"], r["links_missing_count"]) if r["links_checked"] else "[!!] لم تُفحص (%s)" % r.get("links_error", "غير متاح")))
    print("النتيجة:", "[OK]" if r["ok"] else "[!!] لم تتطابق")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

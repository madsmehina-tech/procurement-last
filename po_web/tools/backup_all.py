# -*- coding: utf-8 -*-
r"""python tools\backup_all.py [--dest D:\PO_BackupSet] [--db file.accdb] [--archive D:\PO_Archive]
ONE backup of the database AND the archive together (default: the paths in config.ini; destination: [storage] backup_archive_dir).
The first run copies the whole archive (slow); later runs copy only new / changed files. Nothing is ever deleted in the destination."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import storage_tools as st


def arg(name, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def main():
    dest = arg("--dest", config.BACKUP_ARCHIVE_DIR)
    if not dest:
        print("[!!] حدّد --dest أو ضع backup_archive_dir في config.ini (يجب أن يكون على قرص آخر).")
        return 2
    try:
        res = st.backup_set(arg("--db", config.DB_PATH), arg("--archive", config.ARCHIVE_ROOT), dest, progress=lambda n: print("  ... %d ملف" % n, flush=True),
                            link_reader=st.read_document_paths)
    except (st.ProductionGuard, OSError) as e:
        print("[!!] فشل:", e)
        return 1
    print("[OK] نسخة القاعدة:", res["db_copy"])
    print("     الأرشيف: %d ملف (%s) · نُسخ الآن %d ملفاً بسرعة %s م.ب/ث" % (res["archive_files"], st._human(res["archive_bytes"]), res["copied"], res["mb_per_second"]))
    if not res["db_stable"]:
        print("[!!] تغيّر ملف القاعدة أثناء نسخه؛ كرّر النسخ والنظام متوقف عن الحفظ.")
    if res["errors"]:
        print("[!!] ملفات/عناصر لم تُنسخ (%d) مثل: %s" % (res["failed"], res["errors"][0]))
    lk = res["links"]
    if lk["checked"]:
        print("     روابط المستندات: %d رابطاً فُحصت، المفقود منها %d%s" % (lk["paths"], lk["missing"], (" مثل " + lk["examples"][0]) if lk["examples"] else ""))
    else:
        print("[!!] لم يُتحقق من روابط المستندات في نسخة القاعدة (%s)" % lk.get("error", "غير متاح"))
    print("     القائمة:", res["manifest"])
    print("     الحكم:", "نسخة مكتملة ومثبتة" if res["ok"] and lk["checked"] else ("نسخة مكتملة لكن روابط المستندات لم تُفحص" if res["ok"] else "[!!] نسخة غير مكتملة - لا تعتمد عليها"))
    return 0 if res["ok"] and lk["checked"] else 1


if __name__ == "__main__":
    sys.exit(main())

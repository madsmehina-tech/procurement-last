r"""python tools\backup_now.py  - makes a dated copy of the database right now (into the backups folder)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import backup
import config


def main():
    try:
        dest = backup.backup_database("manual", with_archive=True)
    except OSError as e:
        print("[!!] فشل النسخ:", e)
        return 1
    if not dest:
        print("[!!] ملف القاعدة غير موجود:", config.DB_PATH)
        return 1
    print("[OK] تمت النسخة الاحتياطية:", dest)
    print("     الاحتفاظ بآخر %d نسخة فقط." % config.BACKUP_KEEP)
    if config.BACKUP_ARCHIVE_DIR:
        err = backup._STATE.get("archive_error")
        print("     نسخة الأرشيف مع القاعدة: " + ("[!!] " + err if err else "تمت في " + config.BACKUP_ARCHIVE_DIR))
    else:
        print("     (ملفات الأرشيف غير مشمولة: أضف backup_archive_dir في config.ini)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

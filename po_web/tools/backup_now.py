r"""python tools\backup_now.py  - makes a dated copy of the database right now (into the backups folder)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import backup
import config


def main():
    try:
        dest = backup.backup_database("manual")
    except OSError as e:
        print("[!!] فشل النسخ:", e)
        return 1
    if not dest:
        print("[!!] ملف القاعدة غير موجود:", config.DB_PATH)
        return 1
    print("[OK] تمت النسخة الاحتياطية:", dest)
    print("     الاحتفاظ بآخر %d نسخة فقط." % config.BACKUP_KEEP)
    return 0


if __name__ == "__main__":
    sys.exit(main())

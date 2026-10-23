# -*- coding: utf-8 -*-
r"""
python tools\check_archive.py   -  a read-only check of the whole archive (changes nothing):
  1) records whose file is missing   2) files on disk that no record knows   3) orders without folder / PDF / data copy
Run it after moving the archive, after a restore from backup, and from time to time.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import archive_health
import config


def main():
    print("الأرشيف:", config.ARCHIVE_ROOT)
    r = archive_health.scan()
    print("سجلات المستندات: %d | ملفات على القرص: %d\n" % (r["records"], r["files_on_disk"]))
    print("[%s] سجلات تشير إلى ملف غير موجود: %d" % ("OK" if not r["missing"] else "!!", len(r["missing"])))
    for m in r["missing"][:30]:
        print("     -", m["what"], "->", m["path"])
    print("[%s] ملفات على القرص لا يعرفها النظام: %d" % ("OK" if not r["orphans"] else "!!", len(r["orphans"])))
    for p in r["orphans"][:30]:
        print("     -", p)
    o = r["orders"]
    print("[%s] أوامر بلا مجلد أرشيف: %d" % ("OK" if not o["no_folder"] else "!!", len(o["no_folder"])), o["no_folder"][:10] or "")
    print("[%s] أوامر بلا ملف PDF محفوظ: %d" % ("OK" if not o["no_pdf"] else "..", len(o["no_pdf"])), o["no_pdf"][:10] or "")
    if o["no_pdf"]:
        print("     لإنشائها دفعة واحدة:  python tools\\make_po_pdfs.py")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

r"""
python tools\find_db.py E:\      - finds the database / Excel / uploads folders on a drive, so you know exactly
what to put in config.ini. Only reads; changes nothing.
"""
import os
import sys
import time

SKIP = {"$recycle.bin", "system volume information", "windows", "program files", "program files (x86)", "node_modules", "__pycache__"}


def human(n):
    return "%.1f MB" % (n / 1048576.0) if n >= 1048576 else "%.0f KB" % (n / 1024.0)


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    print("البحث في:", root, "(قد يستغرق دقيقة على قرص خارجي)\n")
    dbs, books, uploads = [], [], []
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d.lower() not in SKIP]
        for f in files:
            low = f.lower()
            p = os.path.join(folder, f)
            if low.endswith((".accdb", ".mdb")) and not low.startswith("~"):
                dbs.append(p)
            elif low.endswith((".xlsm", ".xlsb")) and not low.startswith("~$"):
                books.append(p)
        if os.path.basename(folder).lower() == "uploads":
            uploads.append(folder)

    def show(title, items, size=True):
        print(title)
        if not items:
            print("   (لا يوجد)")
        for p in items:
            try:
                st = os.stat(p)
                extra = "  %s  آخر تعديل %s" % (human(st.st_size) if size and os.path.isfile(p) else "", time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)))
            except OSError:
                extra = ""
            print("  -", p, extra)
        print()

    show("ملفات قواعد البيانات (.accdb / .mdb):", dbs)
    show("ملفات الإكسل بماكرو (.xlsm):", books)
    show("مجلدات uploads (ملفات النسخة القديمة):", uploads, size=False)
    print("القاعدة الصحيحة غالبًا هي PurchaseSystem.accdb الأحدث تعديلًا. إن وُجد أكثر من واحد فاسأل: أيها استُخدم آخر مرة؟")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
r"""
Where is my data?  A standalone, read-only helper (standard library only: it works with ANY version of the system, old or new, and imports nothing from it).

    python where_is_my_data.py "D:\po_web"                         the folder of the system you run now (the program folder)
    python where_is_my_data.py "D:\po_web" --release po_web_v27_7.zip   ... and say what would be LEFT BEHIND if you only moved config.ini + instance
    python where_is_my_data.py "D:\PO_TEST\app" --must-be-inside "D:\PO_TEST"   a TEST install: fails if any of its paths points outside D:\PO_TEST

It shows, for the database, the archive, the old uploads, the backups, the logs: the path really in use, WHERE that setting comes from
(environment variable / config.ini / built-in default), whether it exists, how big it is, and whether it sits INSIDE the program folder
(then a «move config.ini and instance only» install would leave it behind). It shows where the session key comes from (never its value).
It writes nothing and changes nothing.
"""
import configparser
import os
import sys
import zipfile
import hashlib

SETTINGS = [  # label, env var, section, key, default (relative defaults are measured from the program folder)
    ("قاعدة البيانات", "PO_DB_PATH", "database", "path", r"C:\PurchaseSystem.accdb"),
    ("الأرشيف (ملفات PDF)", "PO_ARCHIVE_ROOT", "storage", "archive_root", "archive"),
    ("المرفقات القديمة (LEGACY_UPLOADS)", "PO_UPLOADS_PATH", "storage", "legacy_uploads", "uploads"),
    ("النسخ الاحتياطية للقاعدة", "PO_BACKUP_DIR", "storage", "backup_dir", "backups"),
    ("مكان ثانٍ لنسخ القاعدة", "PO_BACKUP_EXTRA_DIR", "storage", "backup_extra_dir", ""),
    ("نسخة القاعدة + الأرشيف معاً", "PO_BACKUP_ARCHIVE_DIR", "storage", "backup_archive_dir", ""),
    ("السجلات (logs)", "PO_LOG_DIR", "server", "log_dir", "logs"),
]


def read_ini(path):
    """(parser, encoding name) - UTF-8 with/without BOM, UTF-16, Arabic / Western code pages."""
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-16", "cp1256", "cp1252"):
        try:
            text = raw.decode(enc)
            cp = configparser.RawConfigParser()
            cp.read_string(text)
            return cp, enc + (" (BOM)" if raw.startswith(b"\xef\xbb\xbf") else "")
        except (UnicodeError, configparser.Error):
            continue
    return None, "?"


def _is_abs(value):
    return os.path.isabs(value) or value[:2] == "\\\\" or (len(value) > 2 and value[1] == ":" and value[2] in "\\/")      # also a Windows path when this runs elsewhere


def resolve(value, base):
    value = os.path.expandvars(value)
    return os.path.normpath(value if _is_abs(value) else os.path.join(base, value))


def inside(child, parent):
    c, p = os.path.normcase(os.path.realpath(child)), os.path.normcase(os.path.realpath(parent))
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


def size_of(path, limit_seconds=20):
    import time
    t0, n, total = time.time(), 0, 0
    if os.path.isfile(path):
        return 1, os.path.getsize(path), False
    for d, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(d, f))
                n += 1
            except OSError:
                pass
        if time.time() - t0 > limit_seconds:
            return n, total, True
    return n, total, False


def human(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return ("%d %s" % (n, u)) if u == "B" else ("%.1f %s" % (n, u))
        n /= 1024.0


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main(argv):
    for _n in ("stdout", "stderr"):                         # Arabic output must not crash when redirected to a file
        try:
            getattr(sys, _n).reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                   # noqa
            pass
    args = [a for a in argv[1:] if not a.startswith("--")]
    opt = lambda n: argv[argv.index(n) + 1] if n in argv else None
    prog = os.path.abspath(args[0] if args else os.getcwd())
    if not os.path.isfile(os.path.join(prog, "serve.py")) and not os.path.isfile(os.path.join(prog, "config.py")):
        print("[!!] هذا ليس مجلد البرنامج (لا يوجد serve.py). مرّر مسار مجلد po_web.")
        return 2
    must = opt("--must-be-inside")
    bad = 0
    print("مجلد البرنامج:", prog)
    ini_path = os.environ.get("PO_CONFIG") or os.path.join(prog, "config.ini")
    cp = None
    if os.environ.get("PO_CONFIG"):
        print("[!] متغير البيئة PO_CONFIG يحدد ملف الإعداد: " + ini_path)
    if os.path.isfile(ini_path):
        cp, enc = read_ini(ini_path)
        print("ملف الإعداد: %s (الترميز: %s)%s" % (ini_path, enc, "" if cp else "  [!!] تعذّرت قراءته"))
        if cp is None:
            bad += 1
    else:
        print("ملف الإعداد: غير موجود (%s) ← كل القيم من الافتراضيات!" % ini_path)
    print("\nالمسارات الفعلية:")
    left = []
    for label, env, section, key, default in SETTINGS:
        source, val = "الافتراضي", default
        if cp is not None and cp.has_option(section, key) and cp.get(section, key).strip().strip('"'):
            source, val = "config.ini", cp.get(section, key).strip().strip('"')
        if os.environ.get(env):
            source, val = "متغير البيئة " + env, os.environ[env]
        if not val:
            print("  - %-34s (غير مضبوط)" % label)
            continue
        path = resolve(val, prog)
        info = ""
        if os.path.exists(path):
            n, total, cut = size_of(path)
            info = "موجود · %d ملف · %s%s" % (n, human(total), " (قياس جزئي)" if cut else "")
        else:
            info = "غير موجود"
        flags = []
        if inside(path, prog):
            flags.append("داخل مجلد البرنامج ← سيُترك خلفك عند نقل config.ini وinstance فقط!" if os.path.exists(path) else "داخل مجلد البرنامج")
            if os.path.exists(path):
                left.append((label, path))
        if source == "الافتراضي" and key in ("path",):
            flags.append("قيمة افتراضية: تأكد أنها الصحيحة")
        if must and not inside(path, must):
            flags.append("[!!] خارج %s" % must)
            bad += 1
        print("  - %-34s %s\n      المصدر: %s · %s%s" % (label, path, source, info, ("\n      " + " | ".join(flags)) if flags else ""))
    po_env = sorted(k for k in os.environ if k.startswith("PO_"))
    if po_env:
        print("\nمتغيرات بيئة PO_* مضبوطة (الأسماء فقط، وهي تتغلّب على config.ini): " + "، ".join(po_env))
    print("\nمفتاح الجلسات:")
    inst = os.path.join(prog, "instance")
    if os.environ.get("PO_SECRET_KEY"):
        print("  - المصدر: متغير البيئة PO_SECRET_KEY (لا يُستبدل تلقائياً؛ غيّر قيمته بنفسك إن لزم)")
    elif os.path.isfile(os.path.join(inst, "secret.key")):
        print("  - المصدر: الملف instance\\secret.key (%s)" % ("استُبدل بالنسخة 27.4+" if os.path.isfile(os.path.join(inst, "key_rotated_v27_4")) else "لم يُستبدل بعد: سيُستبدل عند أول تشغيل لـ27.4 أو أحدث"))
    else:
        print("  - لا مفتاح بعد (سيُنشأ عند أول تشغيل)")
    if os.path.isdir(inst):
        print("  - محتويات instance (أسماء فقط): " + "، ".join(sorted(os.listdir(inst))))
    if must:
        for sub in ("instance", "logs"):
            pass
    rel = opt("--release")
    if rel:
        print("\nمقارنة مجلد البرنامج بالإصدار الجديد (%s): ما ليس فيه سيُترك خلفك إن نقلت config.ini وinstance فقط" % os.path.basename(rel))
        with zipfile.ZipFile(rel) as z:
            shipped = {}
            for n in z.namelist():
                if n.endswith("/"):
                    continue
                shipped[n.split("/", 1)[1] if "/" in n and n.split("/", 1)[0] in ("po_web", "PO_Web") else n] = n
            extra, modified = {}, []
            for d, dirs, files in os.walk(prog):
                dirs[:] = [x for x in dirs if x not in ("__pycache__", ".pytest_cache", ".git")]
                for f in files:
                    full = os.path.join(d, f)
                    rp = os.path.relpath(full, prog).replace(os.sep, "/")
                    if rp in ("config.ini",) or rp.startswith("instance/"):
                        continue
                    if rp in shipped:
                        try:
                            if hashlib.sha256(z.read(shipped[rp])).hexdigest() != sha(full):
                                modified.append(rp)
                        except OSError:
                            pass
                    else:
                        top = rp.split("/", 1)[0] if "/" in rp else "(ملفات في الجذر)"
                        e = extra.setdefault(top, [0, 0])
                        e[0] += 1
                        try:
                            e[1] += os.path.getsize(full)
                        except OSError:
                            pass
        if not extra and not modified:
            print("  ✔ لا شيء إضافي: ما عدا config.ini وinstance، كل ملفات المجلد القديم من الإصدار نفسه.")
        for top, (n, total) in sorted(extra.items(), key=lambda kv: -kv[1][1]):
            print("  [!!] %s: %d ملف · %s ليست في الإصدار الجديد ← نسخها أو انقلها قبل التبديل" % (top, n, human(total)))
        if modified:
            print("  [!] ملفات من الإصدار عُدّلت يدوياً عندك (%d): %s%s" % (len(modified), "، ".join(modified[:6]), " ..." if len(modified) > 6 else ""))
    print("\nالخلاصة:")
    if left:
        print("  [!!] بيانات داخل مجلد البرنامج: " + "؛ ".join(l for l, _p in left) + "\n       لا تنقل config.ini وinstance وحدهما: انقل هذه المجلدات أيضاً (أو غيّر مساراتها في config.ini إلى خارج مجلد البرنامج قبل أي تبديل).")
    else:
        print("  ✔ لا بيانات (قاعدة/أرشيف/مرفقات/نسخ/سجلات) داخل مجلد البرنامج وفق الإعداد.")
    if must:
        print("  " + ("[!!] مسارات خارج %s: التثبيت ليس معزولاً" % must if bad else "✔ كل المسارات داخل %s: التثبيت معزول" % must))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

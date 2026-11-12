# -*- coding: utf-8 -*-
"""
PO Setup - EXPORT everything of an installation into ONE package file, and RESTORE it on a new main PC.

Export (the old main PC):  reads <app>\\config.ini to find the database, the archive and the old uploads folder, and writes
    PO_Export_<date>.zip  =  db/<database file>  +  archive/...  +  legacy/...  +  instance/...  +  reference/config.ini  +  manifest.json (size + SHA-256 of every file)
    It only READS the installation. It refuses while the database is open (the server must be stopped: a copy of an open Access file can be inconsistent),
    and it re-reads the finished package and checks every file against its SHA-256 before it says «done».
Restore (the new main PC, inside the installation): the database goes to <base>\\data, the archive to the share folder, the old uploads to <base>\\uploads,
    instance to <app>\\instance. Every file is checked against the manifest. It NEVER overwrites: an existing database or a different existing file stops it.
The paths stored in the database are relative to the archive, so the archive may live in another drive / folder on the new PC.
"""
import configparser
import hashlib
import json
import os
import shutil
import zipfile
from datetime import datetime

FORMAT = 1
CHUNK = 1 << 20


class TransferError(Exception):
    pass


def _sha(fileobj):
    h = hashlib.sha256()
    for b in iter(lambda: fileobj.read(CHUNK), b""):
        h.update(b)
    return h.hexdigest()


def sha256_file(path):
    with open(path, "rb") as f:
        return _sha(f)


def read_install(app_dir):
    """The data paths of an installation, from its config.ini (relative paths are measured from the program folder, like the system does)."""
    ini = os.path.join(app_dir, "config.ini")
    if not os.path.isfile(ini):
        raise TransferError("لم أجد config.ini في %s — اختر مجلد البرنامج (app) الذي فيه serve.py." % app_dir)
    cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";",))
    with open(ini, "r", encoding="utf-8-sig") as f:
        cp.read_file(f)

    def get(section, key, default):
        try:
            v = os.path.expandvars(cp.get(section, key).strip().strip('"'))
        except (configparser.Error, AttributeError):
            v = ""
        v = v or default
        return v if os.path.isabs(v) else os.path.normpath(os.path.join(app_dir, v))
    return {"app": app_dir, "ini": ini, "db": get("database", "path", r"C:\PurchaseSystem.accdb"), "archive": get("storage", "archive_root", "archive"),
            "legacy": get("storage", "legacy_uploads", "uploads"), "instance": os.path.join(app_dir, "instance")}


def db_is_open(db_path):
    """Access keeps a lock file next to an open database."""
    return os.path.exists(os.path.splitext(db_path)[0] + ".laccdb") or os.path.exists(os.path.splitext(db_path)[0] + ".ldb")


def _walk(root):
    for d, _dirs, files in os.walk(root):
        for f in files:
            full = os.path.join(d, f)
            yield full, os.path.relpath(full, root).replace("\\", "/")


def plan_export(app_dir):
    """What would be exported: (info dict, [(source path, name inside the package)])."""
    info = read_install(app_dir)
    if not os.path.isfile(info["db"]):
        raise TransferError("ملف القاعدة غير موجود: %s" % info["db"])
    items = [(info["db"], "db/" + os.path.basename(info["db"]))]
    arch = os.path.normcase(os.path.abspath(info["archive"]))
    for label, root in (("archive", info["archive"]), ("legacy", info["legacy"]), ("instance", info["instance"])):
        if not os.path.isdir(root):
            continue
        if label == "legacy" and os.path.normcase(os.path.abspath(root)) == arch:
            continue
        for full, rel in _walk(root):
            if rel.lower().endswith((".laccdb", ".ldb", ".part")):
                continue
            items.append((full, "%s/%s" % (label, rel)))
    items.append((info["ini"], "reference/config.ini"))
    return info, items


def export_package(app_dir, dest_dir, progress=None, now=None, allow_open=False):
    """Writes PO_Export_<stamp>.zip into dest_dir. Returns a result dict. Raises TransferError with an Arabic message."""
    info, items = plan_export(app_dir)
    if db_is_open(info["db"]) and not allow_open:
        raise TransferError("القاعدة مفتوحة الآن (الخادم يعمل أو الملف مفتوح في Access). أوقف الخادم أولاً ثم أعد التصدير، حتى تكون النسخة سليمة. (إن كان الخادم متوقفاً فعلاً وبقيت الرسالة: احذف ملف القفل %s ثم أعد المحاولة.)" % (os.path.splitext(os.path.basename(info["db"]))[0] + ".laccdb"))
    dest_dir = os.path.abspath(dest_dir)
    for root in (info["archive"], info["legacy"], os.path.dirname(info["db"]), app_dir):
        r = os.path.normcase(os.path.abspath(root))
        if os.path.normcase(dest_dir) == r or os.path.normcase(dest_dir).startswith(r + os.sep):
            raise TransferError("مجلد التصدير داخل بيانات النظام نفسها (%s). اختر مجلداً مستقلاً، ويُفضَّل قرصاً خارجياً." % root)
    os.makedirs(dest_dir, exist_ok=True)
    total = sum(os.path.getsize(s) for s, _n in items)
    if shutil.disk_usage(dest_dir).free < total * 1.05 + (50 << 20):
        raise TransferError("المساحة الحرة في %s لا تكفي: يلزم نحو %d ميجابايت." % (dest_dir, total // (1 << 20) + 50))
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    final = os.path.join(dest_dir, "PO_Export_%s.zip" % stamp)
    part = final + ".part"
    db_before = (os.path.getsize(info["db"]), os.stat(info["db"]).st_mtime_ns)
    files = []
    try:
        with zipfile.ZipFile(part, "w", zipfile.ZIP_STORED, allowZip64=True) as z:      # PDFs are already compressed: storing is much faster
            for i, (src, name) in enumerate(items):
                h = hashlib.sha256()
                size = 0
                with open(src, "rb") as f, z.open(zipfile.ZipInfo(name, date_time=datetime.fromtimestamp(os.path.getmtime(src)).timetuple()[:6]), "w", force_zip64=True) as out:
                    for b in iter(lambda: f.read(CHUNK), b""):
                        h.update(b)
                        out.write(b)
                        size += len(b)
                files.append({"name": name, "size": size, "sha256": h.hexdigest(), "mtime": int(os.path.getmtime(src))})
                if progress and (i % 50 == 0 or i == len(items) - 1):
                    progress(i + 1, len(items))
            if (os.path.getsize(info["db"]), os.stat(info["db"]).st_mtime_ns) != db_before:
                raise TransferError("تغيّر ملف القاعدة أثناء التصدير (أحدهم حفظ شيئاً). أوقف الخادم وأعد التصدير.")
            manifest = {"product": "po_web", "format": FORMAT, "created": stamp, "db_file": os.path.basename(info["db"]),
                        "source": {"db": info["db"], "archive": info["archive"], "legacy": info["legacy"]},
                        "counts": {k: sum(1 for f in files if f["name"].startswith(k + "/")) for k in ("db", "archive", "legacy", "instance")},
                        "bytes": sum(f["size"] for f in files), "files": files}
            z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        check = verify_package(part)                                                     # read it back: every file against its SHA-256
        if not check["ok"]:
            raise TransferError("الحزمة لم تجتز الفحص بعد كتابتها: %s" % "؛ ".join(check["problems"][:3]))
        os.replace(part, final)
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        raise
    return {"package": final, "files": len(files), "bytes": manifest["bytes"], "counts": manifest["counts"], "package_sha256": sha256_file(final)}


def read_manifest(package):
    try:
        with zipfile.ZipFile(package) as z:
            man = json.loads(z.read("manifest.json").decode("utf-8"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as e:
        raise TransferError("هذا الملف ليس حزمة تصدير سليمة من النظام (%s)." % type(e).__name__)
    if man.get("product") != "po_web" or "files" not in man:
        raise TransferError("هذا الملف ليس حزمة تصدير من النظام.")
    if int(man.get("format", 0)) > FORMAT:
        raise TransferError("هذه الحزمة من إصدار أحدث من أداة التثبيت؛ استعمل أحدث PO_Setup.exe.")
    return man


def verify_package(package):
    """Every file of the package against the manifest (size + SHA-256). {'ok', 'files', 'problems'}"""
    man = read_manifest(package)
    problems = []
    with zipfile.ZipFile(package) as z:
        names = set(z.namelist())
        for f in man["files"]:
            if f["name"] not in names:
                problems.append("ناقص: " + f["name"])
                continue
            try:
                with z.open(f["name"]) as src:
                    good = _sha(src) == f["sha256"]
            except (zipfile.BadZipFile, OSError):
                good = False
            if not good:
                problems.append("تالف: " + f["name"])
    return {"ok": not problems, "files": len(man["files"]), "problems": problems, "manifest": man}


def _safe_join(root, rel):
    p = os.path.normpath(os.path.join(root, *[x for x in rel.split("/") if x and x not in (".", "..")]))
    if os.path.normcase(p) != os.path.normcase(os.path.normpath(root)) and not os.path.normcase(p).startswith(os.path.normcase(os.path.normpath(root)) + os.sep):
        raise TransferError("مسار غير آمن داخل الحزمة: %s" % rel)
    return p


def restore_package(package, db_path, archive_dir, legacy_dir, instance_dir=None, progress=None, dry=False):
    """Puts the content of a package into the places of a NEW installation. Never overwrites: an existing database, or an existing file with a
    different content, stops everything BEFORE anything is written. Returns a result dict."""
    man = read_manifest(package)
    targets = []
    for f in man["files"]:
        kind, _s, rel = f["name"].partition("/")
        if kind == "db":
            targets.append((f, db_path))
        elif kind == "archive":
            targets.append((f, _safe_join(archive_dir, rel)))
        elif kind == "legacy":
            targets.append((f, _safe_join(legacy_dir, rel)))
        elif kind == "instance" and instance_dir:
            targets.append((f, _safe_join(instance_dir, rel)))
    targets.sort(key=lambda t: t[0]["name"].startswith("db/"))                          # the database is written LAST: its presence means the restore finished
    if os.path.exists(db_path):
        raise TransferError("توجد قاعدة بيانات في مكان التثبيت (%s). الاسترداد لا يكتب فوق قاعدة موجودة: اختر مجلد تثبيت جديداً أو انقل الملف الموجود." % db_path)
    same, clash = 0, []
    for f, dst in targets:                                                              # check EVERYTHING first
        if os.path.exists(dst):
            if os.path.isfile(dst) and os.path.getsize(dst) == f["size"] and sha256_file(dst) == f["sha256"]:
                same += 1
            else:
                clash.append(dst)
    if clash:
        raise TransferError("ملفات موجودة في مكان الاسترداد بمحتوى مختلف (%d) مثل: %s — الاسترداد لا يستبدل ملفاً موجوداً. اختر مجلدات فارغة." % (len(clash), clash[0]))
    need = sum(f["size"] for f, _d in targets)
    if not dry:
        for folder in {os.path.dirname(db_path), archive_dir, legacy_dir}:
            os.makedirs(folder, exist_ok=True)
            if shutil.disk_usage(folder).free < need * 1.05:
                raise TransferError("المساحة الحرة في %s لا تكفي للاسترداد (يلزم نحو %d ميجابايت)." % (folder, need // (1 << 20) + 1))
    done, bad = 0, []
    if not dry:
        with zipfile.ZipFile(package) as z:
            for i, (f, dst) in enumerate(targets):
                if os.path.exists(dst):
                    continue                                                           # identical file already there
                if bad and f["name"].startswith("db/"):
                    continue
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                h = hashlib.sha256()
                tmp = dst + ".part"
                try:
                    with z.open(f["name"]) as src, open(tmp, "wb") as out:
                        for b in iter(lambda: src.read(CHUNK), b""):
                            h.update(b)
                            out.write(b)
                    good = h.hexdigest() == f["sha256"]
                except zipfile.BadZipFile:
                    good = False
                if not good:
                    os.remove(tmp)
                    bad.append(f["name"])
                    if f["name"].startswith("db/"):
                        continue
                    break                                                              # stop: the database (written last) must not go in place over a damaged set
                os.replace(tmp, dst)
                try:
                    os.utime(dst, (f["mtime"], f["mtime"]))
                except (OSError, KeyError):
                    pass
                done += 1
                if progress and (i % 50 == 0 or i == len(targets) - 1):
                    progress(i + 1, len(targets))
    counts = man.get("counts", {})
    return {"ok": not bad, "written": done, "already_there": same, "damaged": bad, "expected": len(targets), "counts": counts, "created": man.get("created"), "db_file": man.get("db_file")}

# -*- coding: utf-8 -*-
"""
storage_tools.py - the archive (PDF files OUTSIDE the Access database) and its backup, scan and restore.

  * guard_outputs()   refuses to write anywhere near the REAL data (the production database / archive / backup folders)
  * scan_archive()    read-only statistics of an archive folder: size, files, types, growth per month, long paths, biggest files
  * backup_set()      ONE backup that covers the database AND the archive together:
                        <root>\\db\\          dated copies of the database (the newest BACKUP_KEEP kept)
                        <root>\\archive\\     a mirror of the archive: only new / changed files are copied, nothing is ever deleted
                        <root>\\manifests\\   one list per backup: the database's SHA-256 + every archive file (path, size, time)
                      The database is copied FIRST and the archive AFTER it: a file is written before its record, so a database copy
                      never points at a file that the archive copy lacks (the opposite order could).
  * restore_set()     rebuilds a database file and an archive folder from a backup set into NEW places (never over the real ones)
                      and proves the result (database hash, file sizes, SHA-256 of a sample or of everything).

Nothing here changes the original archive: it is only read. Everything it writes goes to the folders you name, and those
must not be (or contain, or sit inside) the production folders.
"""
import configparser
import gzip
import hashlib
import json
import os
import random
import shutil
import time
from datetime import datetime

import backup as _backup
import config

CHUNK = 1024 * 1024
MANIFEST_VERSION = 1


class ProductionGuard(Exception):
    """A path that must not be written to (it is, contains, or is inside the production data)."""


# ------------------------------------------------------------------------------------------------------------- safety guard
def norm(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def is_inside(child, parent):
    c, p = norm(child), norm(parent)
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


def env_protected():
    """Only what the launcher passed in PO_PROTECT (the REAL data paths). The acceptance process uses this, because its own configuration file
    deliberately points at the COPIES."""
    return [x for x in (os.environ.get("PO_PROTECT", "") or "").split(";") if x.strip()]


def production_paths():
    """The REAL data paths as written in config.ini (read from the file itself, so an environment override that points a tool at a
    copy can never hide them), plus anything listed in PO_PROTECT (separated by ; ) and the paths the running configuration uses."""
    found = []
    cp = configparser.ConfigParser()
    try:
        cp.read(config.CONFIG_FILE, encoding="utf-8")
    except (configparser.Error, OSError):
        pass
    base = os.path.dirname(config.CONFIG_FILE)
    for section, key in (("database", "path"), ("storage", "archive_root"), ("storage", "legacy_uploads"), ("storage", "backup_dir"),
                         ("storage", "backup_extra_dir"), ("storage", "backup_archive_dir")):
        try:
            v = cp.get(section, key).strip().strip('"')
        except (configparser.Error, AttributeError):
            continue
        if v:
            found.append(v if os.path.isabs(v) else os.path.join(base, v))
    found += [x for x in (os.environ.get("PO_PROTECT", "") or "").split(";") if x.strip()]
    return [p for p in found if p]


def guard_outputs(outputs, protected=None):
    """Raises ProductionGuard if any output path is a production path, inside one, or CONTAINS one. `outputs` are folders or files to be written."""
    prot = list(protected) if protected is not None else production_paths()
    for out in outputs:
        for p in prot:
            if is_inside(out, p) or is_inside(p, out):
                raise ProductionGuard("ممنوع الكتابة في «%s»: هذا المسار هو بيانات التشغيل الحقيقية (أو داخلها أو يحتويها). اختر مجلداً مستقلاً." % out)


def guard_sources_are_copies(sources, protected=None):
    """The acceptance tool must be pointed at COPIES: a source that is a production path is refused."""
    prot = list(protected) if protected is not None else production_paths()
    for src in sources:
        for p in prot:
            if norm(src) == norm(p) or is_inside(src, p):
                raise ProductionGuard("«%s» هو مسار بيانات التشغيل نفسه وليس نسخة منه. أنشئ نسخة (انظر الخطوات) ثم أعد التشغيل." % src)


# ------------------------------------------------------------------------------------------------------------- helpers
def sha256_file(path, chunk=CHUNK):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def walk_files(root):
    """Yields (relative path with /, size, mtime int) of every file under root. Read-only. Unreadable entries are skipped (and counted by the caller)."""
    root = os.path.abspath(root)
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            yield None, 0, 0
            continue
        with it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                    elif e.is_file(follow_symlinks=False):
                        st = e.stat(follow_symlinks=False)
                        yield os.path.relpath(e.path, root).replace(os.sep, "/"), st.st_size, int(st.st_mtime)
                except OSError:
                    yield None, 0, 0


def _human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return "%.1f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024.0


# ------------------------------------------------------------------------------------------------------------- scan (read only)
def scan_archive(root, top=10, long_path=240):
    """Statistics of an archive folder. Only reads. Returns a dict (JSON-friendly)."""
    started = time.time()
    root = os.path.abspath(root)
    files = total = zero = errors = 0
    by_ext, by_top, by_month = {}, {}, {}
    biggest, long_paths, oldest, newest, max_len = [], [], None, None, 0
    for rel, size, mtime in walk_files(root):
        if rel is None:
            errors += 1
            continue
        files += 1
        total += size
        zero += 1 if size == 0 else 0
        ext = rel.rsplit(".", 1)[-1].lower() if "." in rel.rsplit("/", 1)[-1] else "(بلا امتداد)"
        e = by_ext.setdefault(ext, [0, 0]); e[0] += 1; e[1] += size
        t = by_top.setdefault(rel.split("/", 1)[0], [0, 0]); t[0] += 1; t[1] += size
        mkey = datetime.fromtimestamp(mtime).strftime("%Y-%m")
        m = by_month.setdefault(mkey, [0, 0]); m[0] += 1; m[1] += size
        oldest = mtime if oldest is None or mtime < oldest else oldest
        newest = mtime if newest is None or mtime > newest else newest
        full = len(os.path.join(root, rel))
        if full > max_len:
            max_len = full
        if full > long_path and len(long_paths) < top:
            long_paths.append([rel, full])
        biggest.append((size, rel))
        if len(biggest) > top * 20:
            biggest = sorted(biggest, reverse=True)[:top]
    biggest = sorted(biggest, reverse=True)[:top]
    months = sorted(by_month)
    last12 = months[-12:]
    monthly_bytes = (sum(by_month[m][1] for m in last12) / len(last12)) if last12 else 0
    monthly_files = (sum(by_month[m][0] for m in last12) / len(last12)) if last12 else 0
    free = None
    try:
        free = shutil.disk_usage(root).free
    except OSError:
        pass
    return {
        "root_name": os.path.basename(root), "files": files, "bytes": total, "human": _human(total), "zero_byte_files": zero, "unreadable_entries": errors,
        "by_extension": {k: {"files": v[0], "bytes": v[1]} for k, v in sorted(by_ext.items(), key=lambda kv: -kv[1][1])},
        "by_top_folder": {k: {"files": v[0], "bytes": v[1]} for k, v in sorted(by_top.items(), key=lambda kv: -kv[1][1])[:30]},
        "by_month": {k: {"files": by_month[k][0], "bytes": by_month[k][1]} for k in months},
        "largest": [{"path": r, "bytes": s} for s, r in biggest], "max_path_length": max_len, "paths_over_%d" % long_path: long_paths,
        "oldest": datetime.fromtimestamp(oldest).isoformat(timespec="seconds") if oldest else None,
        "newest": datetime.fromtimestamp(newest).isoformat(timespec="seconds") if newest else None,
        "growth": growth_projection(total, monthly_bytes, monthly_files), "free_disk_bytes": free, "scan_seconds": round(time.time() - started, 1),
    }


def growth_projection(total_bytes, monthly_bytes, monthly_files=0):
    """Where the archive will be in 1 / 3 / 5 years if the last 12 months' pace continues (an ESTIMATE, not a promise)."""
    return {"avg_monthly_bytes": int(monthly_bytes), "avg_monthly_files": int(monthly_files),
            "after_1_year": int(total_bytes + monthly_bytes * 12), "after_3_years": int(total_bytes + monthly_bytes * 36), "after_5_years": int(total_bytes + monthly_bytes * 60)}


def scan_report_text(s):
    g = s["growth"]
    lines = ["حجم الأرشيف: %s في %d ملف (أكبر مسار %d حرفاً)" % (s["human"], s["files"], s["max_path_length"]),
             "أقدم ملف: %s · أحدث ملف: %s" % (s["oldest"], s["newest"]),
             "النمو الشهري المتوسط (آخر 12 شهراً): %s و%d ملف" % (_human(g["avg_monthly_bytes"]), g["avg_monthly_files"]),
             "التوقع بعد سنة: %s · 3 سنوات: %s · 5 سنوات: %s" % (_human(g["after_1_year"]), _human(g["after_3_years"]), _human(g["after_5_years"]))]
    if s.get("free_disk_bytes") is not None:
        lines.append("المساحة الحرة على قرص هذا المجلد: %s" % _human(s["free_disk_bytes"]))
    for k, v in list(s["by_extension"].items())[:6]:
        lines.append("  .%s: %d ملف · %s" % (k, v["files"], _human(v["bytes"])))
    if s["zero_byte_files"]:
        lines.append("تنبيه: %d ملف حجمه صفر" % s["zero_byte_files"])
    if s["unreadable_entries"]:
        lines.append("تنبيه: %d عنصر تعذّرت قراءته" % s["unreadable_entries"])
    over = [k for k in s if k.startswith("paths_over_")]
    if over and s[over[0]]:
        lines.append("تنبيه: مسارات طويلة قد تتجاوز حد ويندوز: %d (أولها: %s)" % (len(s[over[0]]), s[over[0]][0][0][:80]))
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------------------- the backup set
def _copy_keep_time(src, dest, mtime):
    _backup._safe_copy(src, dest)
    os.utime(dest, (mtime, mtime))                       # the mirror keeps the file's own time (so «changed?» can be answered next time)


def mirror_archive(src_root, dest_root, progress=None, known=None):
    """Copies new / changed files of the archive into the mirror. NEVER deletes anything in the mirror. Returns stats + the file list
    [path, size, time, sha256]. `known` = {path: (size, time, sha256)} from the previous backup: an unchanged file keeps its recorded hash
    (so only new files are read again); a copied file is hashed from the COPY, which proves what is now stored in the mirror."""
    src_root, dest_root = os.path.abspath(src_root), os.path.abspath(dest_root)
    guard_outputs([dest_root], protected=[src_root])
    os.makedirs(dest_root, exist_ok=True)
    copied = skipped = failed = copied_bytes = 0
    entries, errors = [], []
    started = time.time()
    for rel, size, mtime in walk_files(src_root):
        if rel is None:
            failed += 1
            continue
        target = os.path.join(dest_root, *rel.split("/"))
        prev = (known or {}).get(rel)
        try:
            st = os.stat(target)
            if st.st_size == size and int(st.st_mtime) >= mtime:
                skipped += 1
                entries.append([rel, size, mtime, prev[2] if prev and prev[0] == size and prev[1] == mtime else None])
                continue
        except OSError:
            pass
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            _copy_keep_time(os.path.join(src_root, *rel.split("/")), target, mtime)
            copied += 1
            copied_bytes += size
            entries.append([rel, size, mtime, sha256_file(target)])
        except OSError as e:
            entries.append([rel, size, mtime, None])
            failed += 1
            errors.append("%s: %s" % (rel, " ".join(str(e).split())[:80]))
        if progress and (copied + skipped) % 500 == 0:
            progress(copied + skipped)
    seconds = max(time.time() - started, 0.001)
    return {"copied": copied, "skipped": skipped, "failed": failed, "bytes_copied": copied_bytes, "seconds": round(seconds, 1),
            "mb_per_second": round(copied_bytes / 1048576.0 / seconds, 1), "entries": entries, "errors": errors[:20]}


def _write_manifest(path, data):
    tmp = path + ".part"
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def read_manifest(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def _stable_copy_of_db(db_path, dest, lock=None):
    """Copies the database file; if it changed while it was being copied (somebody saved), copies again (up to 3 times)."""
    for attempt in range(3):
        before = os.stat(db_path)
        if lock is not None:                              # inside the server: no save can happen while the file is copied
            with lock:
                _backup._safe_copy(db_path, dest)
        else:
            _backup._safe_copy(db_path, dest)
        after = os.stat(db_path)
        if (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns):
            return attempt + 1
    return 0                                              # kept, but not proven stable: the report says so


def backup_set(db_path, archive_root, dest_root, keep=None, progress=None, now=None, lock=None):
    """The whole job (see the module text). Returns a result dict; raises OSError if the DATABASE copy fails, never for single archive files."""
    now = now or datetime.now()
    keep = keep or config.BACKUP_KEEP
    dest_root = os.path.abspath(dest_root)
    guard_outputs([dest_root], protected=[db_path, archive_root])
    for sub in ("db", "archive", "manifests"):
        os.makedirs(os.path.join(dest_root, sub), exist_ok=True)
    stamp = now.strftime("%Y%m%d_%H%M%S")
    base, ext = os.path.splitext(os.path.basename(db_path))
    db_name = "%s_%s_set%s" % (base, stamp, ext)
    db_dest = os.path.join(dest_root, "db", db_name)
    attempts = _stable_copy_of_db(db_path, db_dest, lock)                       # 1. the database first
    db_hash, db_size = sha256_file(db_dest), os.path.getsize(db_dest)
    known = {}
    previous = list_sets(dest_root)
    if previous:
        try:
            known = {e[0]: (e[1], e[2], e[3] if len(e) > 3 else None) for e in read_manifest(previous[-1])["archive"]["entries"]}
        except (OSError, ValueError, KeyError):
            known = {}
    m = mirror_archive(archive_root, os.path.join(dest_root, "archive"), progress=progress, known=known)          # 2. the archive after it
    manifest = {"version": MANIFEST_VERSION, "created": now.isoformat(timespec="seconds"), "app_version": config.APP_VERSION,
                "db": {"file": db_name, "size": db_size, "sha256": db_hash, "stable_copy": bool(attempts), "attempts": attempts},
                "archive": {"files": len(m["entries"]), "bytes": sum(e[1] for e in m["entries"]), "entries": m["entries"]},
                "copy_stats": {k: m[k] for k in ("copied", "skipped", "failed", "bytes_copied", "seconds", "mb_per_second")}}
    mpath = os.path.join(dest_root, "manifests", "set_%s.manifest.json.gz" % stamp)
    _write_manifest(mpath, manifest)
    _prune_sets(dest_root, keep)
    return {"manifest": mpath, "db_copy": db_dest, "db_sha256": db_hash, "db_stable": bool(attempts), "archive_files": manifest["archive"]["files"],
            "archive_bytes": manifest["archive"]["bytes"], **manifest["copy_stats"], "errors": m["errors"]}


def _prune_sets(dest_root, keep):
    """Keeps the newest `keep` manifests and their database copies. The archive mirror is never pruned."""
    mdir = os.path.join(dest_root, "manifests")
    names = sorted(f for f in os.listdir(mdir) if f.endswith(".manifest.json.gz"))
    for old in names[:-keep] if keep else []:
        try:
            data = read_manifest(os.path.join(mdir, old))
            os.remove(os.path.join(dest_root, "db", data["db"]["file"]))
        except (OSError, ValueError, KeyError):
            pass
        try:
            os.remove(os.path.join(mdir, old))
        except OSError:
            pass


def list_sets(dest_root):
    mdir = os.path.join(dest_root, "manifests")
    return sorted(os.path.join(mdir, f) for f in os.listdir(mdir) if f.endswith(".manifest.json.gz")) if os.path.isdir(mdir) else []


# ------------------------------------------------------------------------------------------------------------- restore into NEW places
def restore_set(set_root, db_out, archive_out, manifest=None, verify="sample", sample=200, progress=None):
    """Rebuilds the database file at `db_out` and the archive folder at `archive_out` from a backup set. Both must be NEW places,
    never production. verify: 'none' | 'sample' (SHA-256 of `sample` random files) | 'full' (SHA-256 of every file)."""
    guard_outputs([db_out, archive_out])
    if os.path.exists(db_out):                                                     # a restore never overwrites anything that already exists
        raise OSError("ملف الاسترجاع موجود مسبقاً: %s - اختر اسماً جديداً (لا يُكتب فوق ملف موجود)." % db_out)
    if os.path.isdir(archive_out) and os.listdir(archive_out):
        raise OSError("مجلد الاسترجاع ليس فارغاً: %s - اختر مجلداً جديداً أو فارغاً." % archive_out)
    mpath = manifest or (list_sets(set_root) or [None])[-1]
    if not mpath:
        raise OSError("لا توجد نسخة (manifest) في %s" % set_root)
    man = read_manifest(mpath)
    need = man["archive"]["bytes"] + man["db"]["size"]
    os.makedirs(archive_out, exist_ok=True)
    free = shutil.disk_usage(archive_out).free
    if free < need * 1.05:
        raise OSError("المساحة الحرة لا تكفي للاسترجاع: يلزم %s وتوجد %s" % (_human(need), _human(free)))
    started = time.time()
    db_src = os.path.join(set_root, "db", man["db"]["file"])
    if not os.path.isfile(db_src):
        raise OSError("نسخة القاعدة غير موجودة: %s" % man["db"]["file"])
    os.makedirs(os.path.dirname(os.path.abspath(db_out)) or ".", exist_ok=True)
    _backup._safe_copy(db_src, db_out)
    result = {"manifest": os.path.basename(mpath), "created": man["created"], "db_ok": sha256_file(db_out) == man["db"]["sha256"],
              "archive_expected": man["archive"]["files"], "restored": 0, "missing_in_backup": [], "size_mismatch": [], "hash_checked": 0, "hash_mismatch": []}
    mirror = os.path.join(set_root, "archive")
    for entry in man["archive"]["entries"]:
        rel, size, mtime = entry[0], entry[1], entry[2]
        src, dst = os.path.join(mirror, *rel.split("/")), os.path.join(archive_out, *rel.split("/"))
        if not os.path.isfile(src):
            result["missing_in_backup"].append(rel)
            continue
        if os.path.getsize(src) != size:
            result["size_mismatch"].append(rel)                   # a file replaced after the set was made: reported, still restored
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        _copy_keep_time(src, dst, mtime)
        result["restored"] += 1
        if progress and result["restored"] % 500 == 0:
            progress(result["restored"])
    entries = man["archive"]["entries"]
    pick = entries if verify == "full" else (random.Random(1).sample(entries, min(sample, len(entries))) if verify == "sample" else [])
    for entry in pick:
        rel, recorded = entry[0], (entry[3] if len(entry) > 3 else None)
        a, b = os.path.join(mirror, *rel.split("/")), os.path.join(archive_out, *rel.split("/"))
        if os.path.isfile(a) and os.path.isfile(b):
            result["hash_checked"] += 1
            # the hash recorded when the file was backed up (catches damage to the mirror itself); else mirror vs restored copy
            if sha256_file(b) != (recorded or sha256_file(a)):
                result["hash_mismatch"].append(rel)
    result["seconds"] = round(time.time() - started, 1)
    result["ok"] = bool(result["db_ok"] and not result["missing_in_backup"] and not result["hash_mismatch"] and result["restored"] == result["archive_expected"])
    for k in ("missing_in_backup", "size_mismatch", "hash_mismatch"):
        result[k + "_count"] = len(result[k])
        result[k] = result[k][:20]
    return result

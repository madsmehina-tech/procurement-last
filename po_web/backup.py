# -*- coding: utf-8 -*-
"""
backup.py - dated copies of the database file.

  * before any migration touches the database (migrations/runner.py);
  * on demand:  python tools\\backup_now.py ;
  * AUTOMATICALLY while the server runs (start_auto, called by serve.py): a copy when the last automatic copy is older than
    [storage] backup_auto_hours (default 12), in the backup folder AND in [storage] backup_extra_dir (a second place) when it is set.
    The copy is taken while the application's write lock is held, so it never catches a half-written file.

A copy is only ever published under its final name when it is COMPLETE:
  1. it is written to  <name>.part ,
  2. its size is compared with the database file,
  3. only then it is renamed to its final dated name (an atomic step) and its modification time is set to NOW.
A disk that fills up or a USB drive pulled out in the middle therefore leaves a *.part file that is deleted at once - never a broken
file that looks like a good backup. The age of a backup is its OWN time (not the time the database was last written).

Old copies are removed separately for each kind (auto / before_m0xx / manual): the newest BACKUP_KEEP of each kind stay, and for the
automatic kind also the newest copy of every one of the last 30 days, so the history reaches back a month, not a few hours.
"""
import logging
import os
import re
import shutil
import threading
import time
from datetime import datetime, timedelta

import config

log = logging.getLogger("po.error")
_STATE = {"last_error": None}
_DAILY_DAYS = 30


def _stamp_name(reason):
    base, ext = os.path.splitext(os.path.basename(config.DB_PATH))
    return "%s_%s_%s%s" % (base, datetime.now().strftime("%Y%m%d_%H%M%S"), reason, ext)


def _safe_copy(src, dest):
    """Copies src to dest so that dest is either complete or absent. Raises OSError (and leaves nothing behind) on any failure."""
    part = dest + ".part"
    try:
        shutil.copyfile(src, part)
        if os.path.getsize(part) != os.path.getsize(src):
            raise OSError("حجم النسخة لا يطابق حجم القاعدة (%d ≠ %d) - ربما امتلأ القرص" % (os.path.getsize(part), os.path.getsize(src)))
        os.utime(part, None)                              # the backup's age is the moment it was made
        os.replace(part, dest)
    except BaseException:
        try:
            if os.path.exists(part):
                os.remove(part)
        except OSError:
            pass
        raise


def backup_database(reason="manual", lock=None):
    """Returns the path of the copy, or None if the database file does not exist. Raises OSError on failure of the MAIN copy.
    A failure of the second place never stops the main copy (it is remembered: see last_error)."""
    src = config.DB_PATH
    if not os.path.isfile(src):
        return None
    os.makedirs(config.BACKUP_DIR, exist_ok=True)
    dest = os.path.join(config.BACKUP_DIR, _stamp_name(reason))
    if lock is not None:
        with lock:
            _safe_copy(src, dest)
    else:
        _safe_copy(src, dest)
    _STATE["last_error"] = None
    prune()
    extra = getattr(config, "BACKUP_EXTRA_DIR", "")
    if extra:
        try:
            os.makedirs(extra, exist_ok=True)
            _safe_copy(dest, os.path.join(extra, os.path.basename(dest)))
            prune(extra)
        except OSError as e:                              # the second place is down (disk removed, share offline): say it, do not stop
            _STATE["last_error"] = "تعذّر النسخ للمكان الثاني: %s" % " ".join(str(e).split())[:120]
            log.warning("backup extra copy failed: %s", e)
    return dest


def _pattern():
    base, ext = os.path.splitext(os.path.basename(config.DB_PATH))
    # exactly  <base>_YYYYMMDD_HHMMSS_<kind><ext>  : a foreign file (PurchaseSystem_BEFORE_DRILL.accdb ...) is never counted or deleted
    return re.compile(r"^%s_(\d{8})_(\d{6})_(.+)%s$" % (re.escape(base), re.escape(ext)))


def _kind(filename):
    m = _pattern().match(filename)
    return m.group(3) if m else "manual"


def _list(folder):
    pat = _pattern()
    try:
        return sorted(f for f in os.listdir(folder) if pat.match(f))
    except OSError:
        return []


def prune(folder=None):
    folder = folder or config.BACKUP_DIR
    groups = {}
    for f in _list(folder):
        groups.setdefault(_kind(f), []).append(f)
    for kind, items in groups.items():
        keep = set(items[-config.BACKUP_KEEP:])
        if kind == "auto":                                # the newest copy of each of the last 30 days as well
            cutoff = (datetime.now() - timedelta(days=_DAILY_DAYS)).strftime("%Y%m%d")
            newest_of_day = {}
            for f in items:
                day = _pattern().match(f).group(1)
                if day >= cutoff:
                    newest_of_day[day] = f
            keep |= set(newest_of_day.values())
        for old in items:
            if old not in keep:
                try:
                    os.remove(os.path.join(folder, old))
                except OSError:
                    pass
    for f in os.listdir(folder) if os.path.isdir(folder) else []:     # a *.part left by a crash is never a backup
        if f.endswith(".part") and time.time() - os.path.getmtime(os.path.join(folder, f)) > 3600:
            try:
                os.remove(os.path.join(folder, f))
            except OSError:
                pass


def last_backup(folder=None, kind=None):
    """(path, datetime) of the newest copy, or (None, None)."""
    folder = folder or config.BACKUP_DIR
    files = [f for f in _list(folder) if kind is None or _kind(f) == kind]
    if not files:
        return None, None
    newest = max(files, key=lambda f: os.path.getmtime(os.path.join(folder, f)))
    p = os.path.join(folder, newest)
    return p, datetime.fromtimestamp(os.path.getmtime(p))


def status():
    """For /health and the start banner: no paths, only the age of the newest copy."""
    _p, when = last_backup()
    age = round((datetime.now() - when).total_seconds() / 3600, 1) if when else None
    hours = config.BACKUP_AUTO_HOURS
    warnings = []
    if hours > 0 and (age is None or age > hours * 2 + 1):
        warnings.append("لا توجد نسخة احتياطية حديثة (آخر نسخة: %s)" % ("لا شيء" if age is None else "منذ %s ساعة" % age))
    if _STATE["last_error"]:
        warnings.append(_STATE["last_error"])
    if hours > 0 and not getattr(config, "BACKUP_EXTRA_DIR", ""):
        warnings.append("لا يوجد مكان ثانٍ للنسخ الاحتياطي (backup_extra_dir)")
    return {"last": when.isoformat(timespec="minutes") if when else None, "age_hours": age, "auto_every_hours": hours,
            "second_place": bool(getattr(config, "BACKUP_EXTRA_DIR", "")), "second_place_error": None if not _STATE["last_error"] else "تعذّر النسخ للمكان الثاني (راجع logs)", "warnings": warnings}


def ensure_recent(lock=None):
    """Makes an automatic copy if the last AUTOMATIC one is older than the configured hours. Returns the new path or None."""
    hours = config.BACKUP_AUTO_HOURS
    if hours <= 0:
        return None
    _p, when = last_backup(kind="auto")
    if when and (datetime.now() - when).total_seconds() < hours * 3600:
        return None
    return backup_database("auto", lock=lock)


def start_auto(lock, every_seconds=600):
    """A daemon thread: checks every 10 minutes (and once at start) whether an automatic copy is due."""
    def loop():
        while True:
            try:
                ensure_recent(lock)
            except Exception as e:                        # noqa - a failed backup must never stop the server
                _STATE["last_error"] = "فشل النسخ الاحتياطي التلقائي: %s" % " ".join(str(e).split())[:120]
                log.exception("automatic backup failed")
            time.sleep(every_seconds)
    t = threading.Thread(target=loop, name="po-auto-backup", daemon=True)
    t.start()
    return t

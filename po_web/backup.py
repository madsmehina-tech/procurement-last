# -*- coding: utf-8 -*-
"""
backup.py - dated copies of the database file.

  * before any migration touches the database (migrations/runner.py);
  * on demand:  python tools\\backup_now.py ;
  * AUTOMATICALLY while the server runs (start_auto, called by serve.py): a copy when the last automatic copy is older than
    [storage] backup_auto_hours (default 12), in the backup folder AND in [storage] backup_extra_dir (a second place) when it is set.
    The copy is taken while the application's write lock is held, so it never catches a half-written file.
Old copies beyond BACKUP_KEEP are removed - separately for each kind (auto / before_m0xx / manual), so a pre-migration copy is not
pushed out by the automatic ones.
"""
import logging
import os
import shutil
import threading
import time
from datetime import datetime

import config

log = logging.getLogger("po.error")
_STATE = {"last_error": None}


def _stamp_name(reason):
    base, ext = os.path.splitext(os.path.basename(config.DB_PATH))
    return "%s_%s_%s%s" % (base, datetime.now().strftime("%Y%m%d_%H%M%S"), reason, ext)


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
            shutil.copy2(src, dest)
    else:
        shutil.copy2(src, dest)
    prune()
    extra = getattr(config, "BACKUP_EXTRA_DIR", "")
    if extra:
        try:
            os.makedirs(extra, exist_ok=True)
            shutil.copy2(dest, os.path.join(extra, os.path.basename(dest)))
            prune(extra)
            _STATE["last_error"] = None
        except OSError as e:                              # the second place is down (disk removed, share offline): say it, do not stop
            _STATE["last_error"] = "تعذّر النسخ للمكان الثاني: %s" % " ".join(str(e).split())[:120]
            log.warning("backup extra copy failed: %s", e)
    return dest


def _kind(filename, base, ext):
    tail = filename[len(base) + 1:-len(ext)] if ext else filename[len(base) + 1:]
    parts = tail.split("_", 2)                             # YYYYMMDD _ HHMMSS _ reason
    return parts[2] if len(parts) > 2 else "manual"


def prune(folder=None):
    folder = folder or config.BACKUP_DIR
    base, ext = os.path.splitext(os.path.basename(config.DB_PATH))
    try:
        files = sorted(f for f in os.listdir(folder) if f.startswith(base + "_") and f.endswith(ext))
    except OSError:
        return
    groups = {}
    for f in files:
        groups.setdefault(_kind(f, base, ext), []).append(f)
    for items in groups.values():
        for old in items[:-config.BACKUP_KEEP]:
            try:
                os.remove(os.path.join(folder, old))
            except OSError:
                pass


def last_backup(folder=None, kind=None):
    """(path, datetime) of the newest copy, or (None, None)."""
    folder = folder or config.BACKUP_DIR
    base, ext = os.path.splitext(os.path.basename(config.DB_PATH))
    try:
        files = [f for f in os.listdir(folder) if f.startswith(base + "_") and f.endswith(ext) and (kind is None or _kind(f, base, ext) == kind)]
    except OSError:
        return None, None
    if not files:
        return None, None
    newest = max(files, key=lambda f: os.path.getmtime(os.path.join(folder, f)))
    p = os.path.join(folder, newest)
    return p, datetime.fromtimestamp(os.path.getmtime(p))


def status():
    """For /health and the start banner: no paths, only the age of the newest copy."""
    _p, when = last_backup()
    age = round((datetime.now() - when).total_seconds() / 3600, 1) if when else None
    return {"last": when.isoformat(timespec="minutes") if when else None, "age_hours": age, "auto_every_hours": config.BACKUP_AUTO_HOURS,
            "second_place": bool(getattr(config, "BACKUP_EXTRA_DIR", "")), "second_place_error": _STATE["last_error"]}


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

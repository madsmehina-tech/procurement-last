# -*- coding: utf-8 -*-
"""
config.py - the single place every setting is read from.

Order of precedence for each setting:  environment variable  >  config.ini  >  built-in default.
(The environment variables are the same names used before - PO_DB_PATH etc. - so existing
start-up habits keep working; config.ini is the normal way on the office server.)
"""
import os
import secrets
import configparser

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.environ.get("PO_CONFIG") or os.path.join(BASE_DIR, "config.ini")

def _read_ini(path):
    """Reads config.ini whichever way Notepad saved it (UTF-8, UTF-8 with BOM, or old Arabic ANSI), and turns a
    typing mistake in the file into a clear message instead of a Python traceback."""
    cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";",))   # «backup_auto_hours = 12   ; تعليق» is legal
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return cp                                  # no file: built-in defaults / environment variables are used
    text = None
    for enc in ("utf-8-sig", "cp1256", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    try:
        cp.read_string(text or "")
    except configparser.Error as e:
        raise SystemExit(
            "\n!! في ملف الإعداد خطأ كتابي:\n   %s\n   %s\n"
            "   افتح الملف وتأكد أن كل قسم بين أقواس مربعة مثل [database] وأن كل سطر بصيغة:  الاسم = القيمة\n"
            % (path, str(e).splitlines()[0] if str(e) else "")
        )
    return cp


_cp = _read_ini(CONFIG_FILE)


def _get(section, key, env, default):
    v = os.environ.get(env)
    if v:
        return v.strip()
    try:
        v = _cp.get(section, key).strip().strip('"')
    except (configparser.NoSectionError, configparser.NoOptionError):
        return default
    return v or default


def _path(value):
    """A path from the settings. Absolute paths (D:\\x, \\\\server\\share) are used as written. A RELATIVE path is
    measured from the project folder - so a copy that lives on a removable drive keeps working when Windows
    gives that drive a different letter (E: today, F: tomorrow)."""
    value = os.path.expandvars(value)
    if not os.path.isabs(value):
        value = os.path.join(BASE_DIR, value)
    return os.path.normpath(value)


# ---- database ----------------------------------------------------------------
# How text is exchanged with the Access driver. "auto" = use this PC's Windows ANSI code page (utf-8 on a PC that
# has the "Use Unicode UTF-8" option on, cp1256 on a normal Arabic-locale PC). Set it by hand only if tools\\diagnose_text.py says so.
ANSI_ENCODING = _get("database", "ansi_encoding", "PO_ANSI_ENCODING", "auto").lower()
DB_PATH = _path(_get("database", "path", "PO_DB_PATH", r"C:\PurchaseSystem.accdb"))

# ---- storage -----------------------------------------------------------------
# ARCHIVE_ROOT is a LOCAL path on the main (server) PC - the folder that is also shared on the
# network (e.g. D:\PO_Archive shared as \\SERVER\PO_Archive). The server writes to it directly, so
# team members never need write permission on the share itself; they reach every file through the
# web app. Opening the share in Explorer (read-only) stays possible for browsing.
ARCHIVE_ROOT = _path(_get("storage", "archive_root", "PO_ARCHIVE_ROOT", "archive"))
# Where files uploaded before the archive existed live (read-only fallback so old links keep working).
LEGACY_UPLOADS = _path(_get("storage", "legacy_uploads", "PO_UPLOADS_PATH", "uploads"))
# Optional: how the same folder is reached from the OTHER PCs, e.g. \\\\SERVER\\PO_Archive  (display only).
SHARE_PATH = _get("storage", "share_path", "PO_SHARE_PATH", "").rstrip("\\")
# Dated copies of the database made before every migration (and by tools/backup_now.py). Keep this OUTSIDE the
# database folder, ideally on another disk.
BACKUP_DIR = _path(_get("storage", "backup_dir", "PO_BACKUP_DIR", "backups"))
BACKUP_KEEP = int(_get("storage", "backup_keep", "PO_BACKUP_KEEP", "20"))
# automatic backup of the database while the server runs: a copy every BACKUP_AUTO_HOURS hours (0 = off) in BACKUP_DIR and, when set, in a SECOND
# place (BACKUP_EXTRA_DIR: another disk, a shared folder, OneDrive ...). A copy on the same disk as the database is not a real backup.
BACKUP_AUTO_HOURS = int(_get("storage", "backup_auto_hours", "PO_BACKUP_AUTO_HOURS", "12"))
_extra = (_get("storage", "backup_extra_dir", "PO_BACKUP_EXTRA_DIR", "") or "").strip().strip('"')
BACKUP_EXTRA_DIR = _path(_extra) if _extra else ""
# PDF of every purchase order (written to the order's archive folder). It is produced by Microsoft Edge or Google Chrome
# running without a window - both read Arabic perfectly and one of them is on every office PC. browser_path is only
# needed if neither is found automatically.
PDF_BROWSER = _get("pdf", "browser_path", "PO_PDF_BROWSER", "")
PDF_ARGS = _get("pdf", "extra_args", "PO_PDF_ARGS", "")
PDF_MODE = _get("pdf", "mode", "PO_PDF_MODE", "auto").lower()          # auto | off
# OCR (reading quotes that are photos / scans, or PDFs whose text is damaged). Needs Tesseract installed with the
# Arabic language data. tesseract_path is only needed if tesseract.exe is not found automatically.
TESSERACT_PATH = _get("ocr", "tesseract_path", "PO_TESSERACT_PATH", "")
OCR_LANG = _get("ocr", "language", "PO_OCR_LANG", "ara+eng")
OCR_MODE = _get("ocr", "mode", "PO_OCR_MODE", "auto").lower()        # auto | off
MAX_UPLOAD_MB = int(_get("storage", "max_upload_mb", "PO_MAX_UPLOAD_MB", "50"))

# ---- server ------------------------------------------------------------------
HOST = _get("server", "host", "PO_HOST", "0.0.0.0")
PORT = int(_get("server", "port", "PO_PORT", "5000"))
THREADS = int(_get("server", "threads", "PO_THREADS", "8"))
LOG_DIR = _path(_get("server", "log_dir", "PO_LOG_DIR", "logs"))
ENVIRONMENT = _get("server", "environment", "PO_ENV", "production").lower()   # "staging" shows a banner on every page
SESSION_HOURS = int(_get("server", "session_hours", "PO_SESSION_HOURS", "12"))

APP_VERSION = "25.1"


def secret_key():
    """A fixed key kept in instance/secret.key, created once - so a server restart never logs the
    team out (the old behaviour generated a new random key on every start)."""
    env = os.environ.get("PO_SECRET_KEY")
    if env:
        return env
    folder = os.path.join(BASE_DIR, "instance")
    path = os.path.join(folder, "secret.key")
    os.makedirs(folder, exist_ok=True)
    if os.path.isfile(path):
        with open(path, "r", encoding="ascii") as f:
            k = f.read().strip()
            if k:
                return k
    k = secrets.token_hex(32)
    with open(path, "w", encoding="ascii") as f:
        f.write(k)
    return k

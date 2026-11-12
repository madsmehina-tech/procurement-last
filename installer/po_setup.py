# -*- coding: utf-8 -*-
"""
PO Setup - installs the procurement system on the MAIN PC (the one that keeps the database and the shared archive folder).

It does, in order, what the manual guide says (stages 3 and 5), and nothing else:
  1. checks the PC (Windows 64-bit, administrator, disk space, free port)
  2. Python 3.12 (64-bit, «Add python.exe to PATH») - only if a suitable Python is not already there
  3. Microsoft Access database driver (ACE x64) - only if it is not already there
  4. Tesseract OCR with Arabic (optional)
  5. the folders (program, data, shared archive, old uploads, logs, backups)
  6. the Windows share of the archive folder (optional) with read-only access for the team
  7. unpacks the program into <base>\\app  (and writes config.ini)
  8. a NEW empty Access database (or a copy of an existing one) in <base>\\data
  9. pip install -r requirements.txt, python migrate.py, tools\\setup_check.py, tools\\check_storage.py
 10. the firewall rule (office network + Tailscale only), automatic start, and starting the server (optional)

Safe by design: it never deletes anything, never overwrites an existing database or archive, keeps a log (without passwords) in <base>\\install_log.txt,
and every step can be repeated. `--dry-run` shows what it would do without doing it.

    PO_Setup.exe                          the wizard
    PO_Setup.exe --silent --answers a.json   no window (answers in a JSON file)
    PO_Setup.exe --dry-run --answers a.json  only print the plan
"""
import argparse
import ctypes
import dataclasses
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import po_lan as lan  # noqa: E402
import po_transfer as transfer  # noqa: E402

IS_WINDOWS = os.name == "nt"
SETUP_VERSION = "1.0"

PY_VERSION = "3.12.8"
PY_URL = "https://www.python.org/ftp/python/%s/python-%s-amd64.exe" % (PY_VERSION, PY_VERSION)
ACE_URL = "https://download.microsoft.com/download/3/5/C/35C84C36-661A-44E6-9324-8786B8DBE231/accessdatabaseengine_X64.exe"
TESS_URL = "https://github.com/UB-Mannheim/tesseract/releases/download/v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe"
TESS_SHA256 = "c885fff6998e0608ba4bb8ab51436e1c6775c2bafc2559a19b423e18678b60c9"      # computed from that release asset
ARA_URLS = ["https://github.com/tesseract-ocr/tessdata_fast/raw/main/ara.traineddata", "https://github.com/tesseract-ocr/tessdata/raw/main/ara.traineddata"]
ACCEPTED_PYTHONS = ("3.12", "3.11")      # 3.13+ is not accepted: the pinned database driver package (pyodbc 5.1) has no build for it
ACE_DRIVER_NAME = "Microsoft Access Driver (*.mdb, *.accdb)"
ADMIN_PASSWORD_RULE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d).{8,}$")


# ------------------------------------------------------------------------------------------------------------- answers
@dataclasses.dataclass
class Answers:
    share_folder: str = r"D:\PO_Archive"                  # THE archive folder: the one that is shared on the network
    base: str = r"D:\PO"                                  # program (app), data, uploads, logs
    share_name: str = "PO_Archive"
    create_share: bool = True
    team_group: str = "Authenticated Users"               # read-only access to the share for these; «Users» / a group name / DOMAIN\group also work
    db_mode: str = "new"                                  # new | existing | restore
    restore_package: str = ""                             # restore: a PO_Export_*.zip made on the old main PC (database + archive + old uploads)
    db_existing: str = ""                                 # a COPY of this file is placed in <base>\data (the original is never touched)
    admin_user: str = "admin"
    admin_name: str = "مدير النظام"
    admin_password: str = ""                              # only needed for a new database; never stored or logged
    backup_db_dir: str = ""                               # e.g. E:\PO_Backup\db   (empty = <base>\backups)
    backup_set_dir: str = ""                              # e.g. E:\PO_BackupSet   (database + archive backup; empty = not configured yet)
    port: int = 5000
    install_tesseract: bool = True
    open_firewall: bool = True
    autostart: bool = True
    start_after: bool = True
    replace_existing: bool = False                        # an older program folder is moved aside (app_old_<time>) and its config.ini + instance kept
    zip_path: str = ""                                    # the program package (default: next to this file / inside the exe)
    python_path: str = ""                                 # force a specific python.exe (normally found / installed automatically)
    set_private: bool = True                              # a «Public» network blocks the team: make it «Private» (remembered, undone by «remove»)
    allowed_extra: str = ""                               # extra ranges besides the office subnet, e.g. 192.168.5.0/24, 10.0.0.0/8 (never the whole internet)
    alias_name: str = "poweb"                             # friendly name for the link: http://poweb:5000 (a tagged line in hosts); empty = none
    export_dir: str = ""                                  # where client_import.json is written (default: <base>)
    skip_prereqs: bool = False                            # for tests / machines where Python and the driver are known to be fine

    @property
    def app(self):
        return os.path.join(self.base, "app")

    @property
    def data_dir(self):
        return os.path.join(self.base, "data")

    @property
    def db_path(self):
        return os.path.join(self.data_dir, "PurchaseSystem.accdb")

    @property
    def uploads(self):
        return os.path.join(self.base, "uploads")

    @property
    def logs(self):
        return os.path.join(self.base, "logs")

    @property
    def backup_db(self):
        return self.backup_db_dir or os.path.join(self.base, "backups")

    @property
    def log_file(self):
        return os.path.join(self.base, "install_log.txt")


def derive_defaults(share_folder, base=None):
    """From the share folder the person chose: put everything else on the SAME drive (a sensible default the wizard shows and they can change)."""
    m = re.match(r"^([A-Za-z]:)", share_folder or "")
    drive = m.group(1).upper() if m else "D:"
    a = Answers(share_folder=share_folder)
    a.base = base or (drive + "\\PO")
    last = re.split(r"[\\/]+", (share_folder or "").rstrip("\\/"))[-1]
    a.share_name = re.sub(r"[^\w\-]", "_", last if last and not re.match(r"^[A-Za-z]:$", last) else "PO_Archive")[:30] or "PO_Archive"
    return a


def validate(a, check_paths=True):
    """List of problems (empty = fine). Nothing is touched."""
    errs = []
    if not a.share_folder or not os.path.isabs(a.share_folder) and not re.match(r"^[A-Za-z]:[\\/]", a.share_folder):
        errs.append("حدّد مجلد الشير بمسار كامل (مثل D:\\PO_Archive).")
    if not a.base or not re.match(r"^[A-Za-z]:[\\/]", a.base) and not os.path.isabs(a.base):
        errs.append("حدّد مجلد التثبيت بمسار كامل (مثل D:\\PO).")
    if not (1 <= int(a.port) <= 65535):
        errs.append("رقم المنفذ غير صالح.")
    if a.db_mode == "new" and not ADMIN_PASSWORD_RULE.match(a.admin_password or ""):
        errs.append("كلمة مرور المدير: 8 خانات فأكثر وفيها حرف ورقم.")
    if a.db_mode == "new" and not re.match(r"^[A-Za-z0-9_.\-]{3,50}$", a.admin_user or ""):
        errs.append("اسم مستخدم المدير: حروف إنجليزية وأرقام (3 خانات فأكثر).")
    if a.db_mode == "existing" and a.admin_password and not ADMIN_PASSWORD_RULE.match(a.admin_password):
        errs.append("كلمة مرور المدير: 8 خانات فأكثر وفيها حرف ورقم (أو اتركها فارغة إن كانت القاعدة فيها مدير).")
    if a.db_mode == "existing" and check_paths and not os.path.isfile(a.db_existing or ""):
        errs.append("ملف القاعدة الموجودة غير موجود: %s" % a.db_existing)
    _extra, extra_errs = lan.parse_remote_list(a.allowed_extra)
    errs += extra_errs
    if (a.alias_name or "") and not lan.valid_alias(a.alias_name):
        errs.append("الاسم البديل للرابط: حروف إنجليزية صغيرة وأرقام وشرطة ونقاط بين الأجزاء (مثل poweb أو alhadab.procurement) أو اتركه فارغاً.")
    if a.db_mode == "restore" and check_paths and not os.path.isfile(a.restore_package or ""):
        errs.append("ملف حزمة الاسترداد غير موجود: %s" % a.restore_package)
    if a.db_mode == "restore" and a.admin_password and not ADMIN_PASSWORD_RULE.match(a.admin_password):
        errs.append("كلمة مرور المدير: 8 خانات فأكثر وفيها حرف ورقم (أو اتركها فارغة: حسابات النظام تأتي مع الحزمة).")
    if a.db_mode not in ("new", "existing", "restore"):
        errs.append("خيار القاعدة غير معروف.")
    if os.path.normcase(os.path.abspath(a.share_folder)) == os.path.normcase(os.path.abspath(a.app)) or \
            os.path.normcase(os.path.abspath(a.share_folder)).startswith(os.path.normcase(os.path.abspath(a.app)) + os.sep):
        errs.append("مجلد الشير لا يكون داخل مجلد البرنامج (app).")
    return errs


# ------------------------------------------------------------------------------------------------------------- the config.ini
def build_config(a, tesseract_path=""):
    """The settings file the system reads. All data paths are ABSOLUTE and outside the program folder, so the program can be replaced at any time."""
    host = socket.gethostname()
    lines = ["; Written by PO Setup %s on %s. Paths are absolute and outside the program folder: replacing the program never leaves data behind." % (SETUP_VERSION, datetime.now().strftime("%Y-%m-%d %H:%M")),
             "[database]", "path = %s" % a.db_path, "",
             "[storage]", "archive_root = %s" % a.share_folder,
             "share_path = \\\\%s\\%s" % (host, a.share_name) if a.create_share else "; share_path = \\\\SERVER\\PO_Archive",
             "legacy_uploads = %s" % a.uploads,
             "max_upload_mb = 50",
             "backup_dir = %s" % a.backup_db,
             "backup_keep = 20", "backup_auto_hours = 12"]
    if a.backup_set_dir:
        lines.append("backup_archive_dir = %s" % a.backup_set_dir)
    else:
        lines.append("; To enable the backup of database + archive: a folder on ANOTHER disk (not D:). Remove the ; at the start of the next line, change the path, then run deploy\\backup_all.bat once.")
        lines.append("; backup_archive_dir = F:\\PO_BackupSet")
    lines += ["", "[server]", "host = 0.0.0.0", "port = %d" % int(a.port), "threads = 8", "log_dir = %s" % a.logs, "session_hours = 12", ""]
    if tesseract_path:
        lines += ["[ocr]", "tesseract_path = %s" % tesseract_path, "language = ara+eng", "mode = auto", ""]
    lines += ["[pdf]", "mode = auto", ""]
    return "\r\n".join(lines)


# ------------------------------------------------------------------------------------------------------------- helpers
class Ctx:
    """What a step needs: the answers, a log, dry-run mode, and the found python."""

    def __init__(self, answers, log=None, dry=False, run=None, download=None):
        self.a = answers
        self.dry = dry
        self.python = answers.python_path or ""
        self._log = log or (lambda m: None)
        self.run = run or self._run
        self.download = download or self._download
        self.tesseract = ""
        self.notes = []

    def log(self, msg):
        text = str(msg)
        if self.a.admin_password:
            text = text.replace(self.a.admin_password, "********")
        self._log(text)

    def _run(self, cmd, cwd=None, env=None, timeout=1800):
        """Runs a command; returns (exit code, output). The output is logged. Shell is never used."""
        self.log("$ " + " ".join(str(c) for c in cmd))
        if self.dry:
            return 0, "[dry-run]"
        e = dict(os.environ)
        e.update(env or {})
        try:
            p = subprocess.run(cmd, cwd=cwd, env=e, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as ex:
            return 1, "%s: %s" % (type(ex).__name__, ex)
        out = (p.stdout or b"").decode("utf-8", "replace") + (p.stderr or b"").decode("utf-8", "replace")
        if out.strip():
            self.log(out.strip()[-3000:])
        return p.returncode, out

    def _download(self, url, dest, sha256=None):
        self.log("download " + url)
        if self.dry:
            return dest
        req = urllib.request.Request(url, headers={"User-Agent": "PO-Setup"})
        with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
        if sha256 and sha256_file(dest) != sha256.lower():
            os.remove(dest)
            raise RuntimeError("بصمة الملف المنزَّل لا تطابق المتوقع: لن يُستعمل.")
        return dest


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


PS_PRELUDE = "$env:PSModulePath = [Environment]::GetEnvironmentVariable('PSModulePath','Machine'); "      # (a PowerShell 7 parent leaves a path that Windows PowerShell 5.1 cannot load its own modules from)


def ps(script):
    """Command line that runs a PowerShell script (no profile, no policy problems)."""
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", PS_PRELUDE + script]


def ps_quote(text):
    return "'" + str(text).replace("'", "''") + "'"


def is_admin():
    if not IS_WINDOWS:
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:                                    # noqa
        return False


def fixed_drives():
    """[(drive letter, free bytes, total bytes)] of the fixed disks."""
    out = []
    if not IS_WINDOWS:
        return out
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    for i in range(26):
        if mask & (1 << i):
            root = "%s:\\" % chr(65 + i)
            if ctypes.windll.kernel32.GetDriveTypeW(root) == 3:
                try:
                    u = shutil.disk_usage(root)
                    out.append((root[:2], u.free, u.total))
                except OSError:
                    pass
    return out


def port_free(port):
    s = socket.socket()
    try:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", int(port))) != 0
    finally:
        s.close()


# ------------------------------------------------------------------------------------------------------------- python
def parse_python_version(text):
    m = re.search(r"Python (\d+)\.(\d+)\.(\d+)", text or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def python_ok(version):
    return bool(version) and ("%d.%d" % version[:2]) in ACCEPTED_PYTHONS


def candidate_pythons(extra=None):
    """Possible python.exe paths: the one given, the registry (PythonCore), the py launcher, and PATH."""
    out = [extra] if extra else []
    if IS_WINDOWS:
        import winreg
        for ver in ACCEPTED_PYTHONS:
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(hive, r"SOFTWARE\Python\PythonCore\%s\InstallPath" % ver) as k:
                        base = winreg.QueryValueEx(k, "")[0]
                        out.append(os.path.join(base, "python.exe"))
                except OSError:
                    pass
        for ver in ACCEPTED_PYTHONS:
            out.append("py:-" + ver)
    for name in ("python", "python3"):
        p = shutil.which(name)
        if p:
            out.append(p)
    return [c for c in out if c]


def find_python(ctx):
    """The first candidate that is Python 3.11/3.12 and 64-bit. Returns its python.exe path or ''."""
    for c in candidate_pythons(ctx.a.python_path or None):
        cmd = ["py", c[3:], "-c", "import sys,struct;print(sys.executable);print(sys.version.split()[0]);print(struct.calcsize('P')*8)"] if c.startswith("py:") \
            else [c, "-c", "import sys,struct;print(sys.executable);print(sys.version.split()[0]);print(struct.calcsize('P')*8)"]
        if ctx.dry:
            continue
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            continue
        parts = (p.stdout or b"").decode("utf-8", "replace").split()
        if p.returncode == 0 and len(parts) >= 3 and parts[2] == "64":
            v = parse_python_version("Python " + parts[1])
            if python_ok(v) and os.path.isfile(parts[0]):
                return parts[0]
    return ""


def verify_signature(ctx, path, publisher_contains):
    """Authenticode: the installer must be signed by the publisher we expect (Python Software Foundation / Microsoft Corporation)."""
    if ctx.dry or not IS_WINDOWS:
        return True
    code, out = ctx.run(ps("$s = Get-AuthenticodeSignature -LiteralPath %s; Write-Output ($s.Status.ToString() + '|' + $s.SignerCertificate.Subject)" % ps_quote(path)))
    return code == 0 and out.strip().startswith("Valid") and publisher_contains.lower() in out.lower()


def step_python(c):
    if c.a.skip_prereqs:
        c.python = c.a.python_path or sys.executable
        return "ok", "تخطّي (skip_prereqs): %s" % c.python
    found = find_python(c)
    if found:
        c.python = found
        return "ok", "بايثون مناسب موجود: %s" % found
    tmp = os.path.join(tempfile.gettempdir(), "python-%s-amd64.exe" % PY_VERSION)
    c.download(PY_URL, tmp)
    if not verify_signature(c, tmp, "Python Software Foundation"):
        return "fail", "توقيع مثبّت بايثون غير صالح أو ليس من Python Software Foundation: لن يُشغَّل."
    code, out = c.run([tmp, "/quiet", "InstallAllUsers=1", "PrependPath=1", "Include_pip=1", "Include_launcher=1", "Include_tcltk=1", "Include_test=0", "Shortcuts=0"], timeout=1200)
    if code not in (0, 3010):
        return "fail", "فشل تثبيت بايثون (رمز %s). شغّل المثبّت يدوياً وفعّل «Add python.exe to PATH» ثم أعد هذه الأداة." % code
    if c.dry:
        return "ok", "سيُثبَّت بايثون %s (64-bit) مع إضافته إلى PATH" % PY_VERSION
    found = find_python(c)
    if not found:
        return "fail", "تم التثبيت لكن لم أجد python.exe: أعد تشغيل الجهاز وشغّل الأداة مرة ثانية."
    c.python = found
    return "ok", "ثُبّت بايثون %s: %s" % (PY_VERSION, found)


# ------------------------------------------------------------------------------------------------------------- Access driver
def ace_installed():
    if not IS_WINDOWS:
        return False
    import winreg
    for view in (winreg.KEY_WOW64_64KEY,):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\ODBC\ODBCINST.INI\ODBC Drivers", 0, winreg.KEY_READ | view) as k:
                try:
                    return "install" in str(winreg.QueryValueEx(k, ACE_DRIVER_NAME)[0]).lower()
                except OSError:
                    return False
        except OSError:
            return False
    return False


def office_32bit_present():
    """Microsoft Office 32-bit blocks installing the 64-bit driver (the two cannot live together)."""
    if not IS_WINDOWS:
        return False
    import winreg
    for path in (r"SOFTWARE\Microsoft\Office\ClickToRun\Configuration", r"SOFTWARE\WOW6432Node\Microsoft\Office\ClickToRun\Configuration"):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as k:
                if str(winreg.QueryValueEx(k, "Platform")[0]).lower() == "x86":
                    return True
        except OSError:
            pass
    return False


def step_ace(c):
    if c.a.skip_prereqs:
        return "ok", "تخطّي (skip_prereqs)"
    if ace_installed():
        return "ok", "سائق Access (64-bit) موجود"
    if office_32bit_present():
        return "fail", ("على الجهاز Office 32-bit ولا يجتمع مع سائق Access 64-bit. الحل: ثبّت بايثون 32-bit وسائق 32-bit معاً، أو استعمل جهازاً بلا Office 32-bit. "
                        "أخبرني بهذه الرسالة قبل المتابعة.")
    tmp = os.path.join(tempfile.gettempdir(), "accessdatabaseengine_X64.exe")
    c.download(ACE_URL, tmp)
    if not verify_signature(c, tmp, "Microsoft"):
        return "fail", "توقيع مثبّت سائق Access غير صالح أو ليس من Microsoft: لن يُشغَّل."
    code, out = c.run([tmp, "/quiet"], timeout=1200)
    if code not in (0, 3010):
        return "fail", "فشل تثبيت سائق Access (رمز %s). حمّله يدوياً من موقع Microsoft: «Microsoft Access Database Engine 2016 Redistributable» x64 ثم أعد الأداة." % code
    if c.dry:
        return "ok", "سيُثبَّت سائق Access (ACE x64)"
    if not ace_installed():
        return "fail", "انتهى التثبيت لكن السائق لا يظهر: أعد تشغيل الجهاز ثم أعد هذه الأداة."
    return "ok", "ثُبّت سائق Access (64-bit)"


# ------------------------------------------------------------------------------------------------------------- Tesseract
def step_tesseract(c):
    if not c.a.install_tesseract:
        return "skip", "لم يُطلب (يمكن إضافته لاحقاً)"
    exe = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if not os.path.isfile(exe) or c.dry:
        tmp = os.path.join(tempfile.gettempdir(), "tesseract-setup.exe")
        c.download(TESS_URL, tmp, TESS_SHA256)
        code, out = c.run([tmp, "/S"], timeout=1200)
        if code != 0:
            return "warn", "فشل تثبيت Tesseract (رمز %s): قراءة صور عروض الأسعار لن تعمل، وباقي النظام يعمل." % code
    tessdata = os.path.join(os.path.dirname(exe), "tessdata", "ara.traineddata")
    if not os.path.isfile(tessdata) or c.dry:
        ok = False
        for url in ARA_URLS:
            try:
                c.download(url, tessdata)
                ok = c.dry or os.path.getsize(tessdata) > 500000
                if ok:
                    break
            except Exception as e:                       # noqa
                c.log("ara download failed: %s" % e)
        if not ok:
            return "warn", "ثُبّت Tesseract لكن تعذّر تنزيل اللغة العربية (ara.traineddata): ضعه يدوياً في مجلد tessdata."
    c.tesseract = exe
    return "ok", "Tesseract مع العربية: %s" % exe


# ------------------------------------------------------------------------------------------------------------- folders, share
def step_folders(c):
    a = c.a
    made = []
    for d in (a.base, a.app, a.data_dir, a.share_folder, a.uploads, a.logs, a.backup_db) + ((a.backup_set_dir,) if a.backup_set_dir else ()):
        if not os.path.isdir(d):
            made.append(d)
            c.log("mkdir " + d)
            if not c.dry:
                os.makedirs(d, exist_ok=True)
    return "ok", ("أُنشئت: %s" % "، ".join(made)) if made else "المجلدات موجودة"


def team_group_ps(group):
    """PowerShell expression that gives the real (localized) account name of the team group."""
    sids = {"authenticated users": "S-1-5-11", "users": "S-1-5-32-545", "everyone": "S-1-1-0"}
    sid = sids.get((group or "").strip().lower())
    if sid:
        return "(New-Object System.Security.Principal.SecurityIdentifier(%s)).Translate([System.Security.Principal.NTAccount]).Value" % ps_quote(sid)
    return ps_quote(group)


def step_share(c):
    a = c.a
    if not a.create_share:
        return "skip", "لم يُطلب إنشاء المشاركة (تجاهلها إن كانت موجودة)"
    me = "%s\\%s" % (os.environ.get("USERDOMAIN", ""), os.environ.get("USERNAME", "")) if os.environ.get("USERNAME") else ""
    script = ("$ErrorActionPreference='Stop'; $team = " + team_group_ps(a.team_group) + "; $me = " + ps_quote(me) + "; "
              "$ex = Get-SmbShare -Name " + ps_quote(a.share_name) + " -ErrorAction SilentlyContinue; "
              "if ($ex) { if ($ex.Path -ne " + ps_quote(a.share_folder) + ") { throw 'يوجد مشاركة بالاسم نفسه على مسار آخر: ' + $ex.Path } else { Write-Output 'EXISTS' } } "
              "else { if ($me) { New-SmbShare -Name " + ps_quote(a.share_name) + " -Path " + ps_quote(a.share_folder) + " -ReadAccess $team -FullAccess $me | Out-Null } "
              "else { New-SmbShare -Name " + ps_quote(a.share_name) + " -Path " + ps_quote(a.share_folder) + " -ReadAccess $team | Out-Null }; Write-Output 'CREATED' }; "
              "icacls " + ps_quote(a.share_folder) + " /grant ($team + ':(OI)(CI)RX') | Out-Null; "
              "if ($me) { icacls " + ps_quote(a.share_folder) + " /grant ($me + ':(OI)(CI)F') | Out-Null }")
    code, out = c.run(ps(script))
    if code != 0:
        return "warn", "تعذّر إنشاء المشاركة تلقائياً (%s). أنشئها يدوياً: كليك يمين على المجلد ← مشاركة متقدمة، قراءة فقط للفريق." % " ".join(out.split())[-160:]
    return "ok", "المشاركة \\\\%s\\%s (الفريق قراءة فقط: %s)" % (socket.gethostname(), a.share_name, a.team_group)


# ------------------------------------------------------------------------------------------------------------- program
def find_zip(a):
    if a.zip_path:
        return a.zip_path
    here = [os.path.dirname(os.path.abspath(__file__)), getattr(sys, "_MEIPASS", ""), os.path.dirname(sys.executable)]
    for d in here:
        if d and os.path.isdir(d):
            for f in sorted(os.listdir(d), reverse=True):
                if re.match(r"^po_web_v[\d_]+\.zip$", f):
                    return os.path.join(d, f)
    return ""


def verify_zip_hash(zip_path):
    """If a «<zip>.sha256» file sits next to the zip, the zip must match it."""
    side = zip_path + ".sha256"
    if os.path.isfile(side):
        want = open(side, encoding="utf-8").read().split()[0].strip().lower()
        return sha256_file(zip_path) == want
    return None


def safe_extract(zip_path, dest, skip=()):
    """Unpacks the package (its «po_web/» top folder is flattened). Refuses any path that would leave `dest`. Returns the number of files."""
    n = 0
    dest_abs = os.path.abspath(dest)
    with zipfile.ZipFile(zip_path) as z:
        names = [i for i in z.infolist() if not i.is_dir()]
        prefix = "po_web/" if names and all(i.filename.startswith("po_web/") for i in names) else ""
        for info in names:
            rel = info.filename[len(prefix):]
            if not rel or any(rel.replace("\\", "/").startswith(s) for s in skip):
                continue
            target = os.path.abspath(os.path.join(dest_abs, *rel.split("/")))
            if not (target == dest_abs or target.startswith(dest_abs + os.sep)):
                raise RuntimeError("ملف في الحزمة يحاول الخروج من المجلد: %s" % info.filename)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            n += 1
    return n


def step_unpack(c):
    a = c.a
    zp = find_zip(a)
    if not zp or not os.path.isfile(zp):
        return "fail", "لم أجد حزمة البرنامج po_web_v*.zip بجوار هذه الأداة. ضعها بجانبها (أو حدّدها بـ zip_path)."
    ok = verify_zip_hash(zp)
    if ok is False:
        return "fail", "بصمة حزمة البرنامج لا تطابق ملف .sha256: الحزمة تالفة أو معدّلة. أعد تحميلها."
    app = a.app
    exists = os.path.isfile(os.path.join(app, "serve.py"))
    keep = []
    if exists and not a.replace_existing:
        return "fail", "في %s برنامج مثبَّت بالفعل. للترقية فعّل «استبدال البرنامج القديم» (يُنقل القديم إلى app_old_… ويُحتفظ بـ config.ini وinstance)." % app
    if exists and a.replace_existing:
        old = "%s_old_%s" % (app, datetime.now().strftime("%Y%m%d_%H%M%S"))
        c.log("move %s -> %s" % (app, old))
        if not c.dry:
            os.rename(app, old)
            os.makedirs(app, exist_ok=True)
            for name in ("config.ini", "instance"):
                src = os.path.join(old, name)
                if os.path.exists(src):
                    keep.append(name)
                    (shutil.copytree if os.path.isdir(src) else shutil.copy2)(src, os.path.join(app, name))
    if c.dry:
        return "ok", "سيُفك %s في %s" % (os.path.basename(zp), app)
    n = safe_extract(zp, app, skip=tuple(k + "/" if k == "instance" else k for k in keep))
    return "ok", "فُكّ %d ملفاً في %s%s" % (n, app, ("؛ حُفظ من القديم: " + "، ".join(keep)) if keep else "")


def step_config(c):
    a = c.a
    path = os.path.join(a.app, "config.ini")
    if os.path.isfile(path) and not c.dry:
        return "ok", "config.ini موجود فلم يُستبدل (ترقية)"
    text = build_config(a, c.tesseract)
    c.log("write " + path)
    if not c.dry:
        with open(path, "w", encoding="utf-8", newline="") as f:                 # UTF-8 without BOM
            f.write(text)
        with open(os.path.join(a.app, "deploy", "python_path.txt"), "w", encoding="ascii", errors="replace") as f:
            f.write(c.python)
        with open(os.path.join(a.app, "po.bat"), "w", encoding="ascii", errors="replace", newline="\r\n") as f:
            f.write('@echo off\r\nrem runs python of this installation:  po tools\\setup_check.py\r\nset PYTHONUTF8=1\r\n"%s" %%*\r\n' % c.python)
    return "ok", "كُتب config.ini (UTF-8) وpo.bat"


def step_database(c):
    a = c.a
    if os.path.isfile(a.db_path):
        return "ok", "ملف القاعدة موجود ولم يُلمس: %s" % a.db_path
    if a.db_mode == "restore":
        try:
            r = transfer.restore_package(a.restore_package, a.db_path, a.share_folder, a.uploads, os.path.join(a.app, "instance"), dry=c.dry)
        except transfer.TransferError as e:
            return "fail", str(e)
        if not r["ok"]:
            return "fail", "ملفات تالفة داخل الحزمة (%d) مثل %s — أعد التصدير من الجهاز القديم." % (len(r["damaged"]), r["damaged"][0])
        n = r["counts"]
        return "ok", "استُردّت الحزمة (%s): القاعدة + %d ملف أرشيف + %d ملف مرفقات قديمة — كل ملف طابق بصمته" % (r["created"], n.get("archive", 0), n.get("legacy", 0))
    if a.db_mode == "existing":
        c.log("copy %s -> %s" % (a.db_existing, a.db_path))
        if not c.dry:
            shutil.copy2(a.db_existing, a.db_path)
        return "ok", "نُسخت قاعدتك إلى %s (الأصل لم يُلمس)" % a.db_path
    script = ("$ErrorActionPreference='Stop'; $cat = New-Object -ComObject ADOX.Catalog; "
              "$cat.Create('Provider=Microsoft.ACE.OLEDB.12.0;Data Source=' + " + ps_quote(a.db_path) + "); "
              "[void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($cat)")
    code, out = c.run(ps(script))
    if code != 0 or (not c.dry and not os.path.isfile(a.db_path)):
        return "fail", "تعذّر إنشاء قاعدة Access فارغة (%s). أنشئ ملف Access فارغاً باسم %s يدوياً ثم أعد الأداة." % (" ".join(out.split())[-140:], a.db_path)
    return "ok", "أُنشئت قاعدة Access جديدة: %s" % a.db_path


# ------------------------------------------------------------------------------------------------------------- pip / migrate / checks
def step_pip(c):
    for attempt in (1, 2):
        code, out = c.run([c.python, "-m", "pip", "install", "--disable-pip-version-check", "-r", "requirements.txt"], cwd=c.a.app, timeout=1800)
        if code == 0:
            return "ok", "ثُبّتت المكتبات (pip install -r requirements.txt)"
        c.log("pip failed (attempt %d)" % attempt)
    return "fail", "فشل pip install. تأكد من الإنترنت ثم أعد الأداة. آخر السطور: %s" % " ".join(out.split())[-200:]


def step_migrate(c):
    env = {"PYTHONUTF8": "1"}
    cmd = [c.python, "migrate.py"]
    if c.a.admin_password:                                                            # only used if the database has no admin account yet
        env.update({"PO_ADMIN_USER": c.a.admin_user, "PO_ADMIN_NAME": c.a.admin_name, "PO_ADMIN_PASSWORD": c.a.admin_password})
    if c.a.db_mode == "new":
        cmd.append("--new-database")                                              # a brand-new EMPTY database: build the base tables too
    code, out = c.run(cmd, cwd=c.a.app, env=env, timeout=1800)
    if code != 0 and "Admin username" in out:
        return "fail", "القاعدة المنسوخة لا تحتوي حساب مدير. ارجع إلى صفحة «حساب المدير» وأدخل اسماً وكلمة مرور (8+ وحرف ورقم) ثم أعد التثبيت."
    if code != 0:
        return "fail", "فشل python migrate.py: %s" % " ".join(out.split())[-300:]
    dup = "يوجد تكرار" in out
    return ("warn", "اكتمل الترحيل لكن توجد أرقام مكررة تحتاج تصحيحاً (انظر السجل).") if dup else ("ok", "اكتمل الترحيل (migrate.py)")


def step_checks(c):
    notes, worst = [], "ok"
    for tool in ("setup_check.py", "check_storage.py"):
        code, out = c.run([c.python, os.path.join("tools", tool)], cwd=c.a.app, env={"PYTHONUTF8": "1"}, timeout=900)
        bad = [l for l in out.splitlines() if "[!!]" in l or "✘" in l]
        status = "ok" if code == 0 and not bad else ("fail" if code != 0 else "warn")
        notes.append("%s: %s" % (tool, {"ok": "سليم", "warn": "ملاحظات (%d)" % len(bad), "fail": "فشل"}[status]))
        if status == "fail" or (status == "warn" and worst == "ok"):
            worst = status
        c.notes += bad[:5]
    return worst, "؛ ".join(notes)


# ------------------------------------------------------------------------------------------------------------- firewall, autostart, start
def step_firewall(c):
    if not c.a.open_firewall:
        return "skip", "لم يُطلب"
    p = int(c.a.port)
    cmds = [["netsh", "advfirewall", "firewall", "delete", "rule", "name=PO Web (LAN)"], ["netsh", "advfirewall", "firewall", "delete", "rule", "name=PO Web (Tailscale)"],
            ["netsh", "advfirewall", "firewall", "add", "rule", "name=PO Web (LAN)", "dir=in", "action=allow", "protocol=TCP", "localport=%d" % p, "profile=private,domain", "remoteip=" + ",".join(["LocalSubnet"] + lan.parse_remote_list(c.a.allowed_extra)[0])],
            ["netsh", "advfirewall", "firewall", "add", "rule", "name=PO Web (Tailscale)", "dir=in", "action=allow", "protocol=TCP", "localport=%d" % p, "remoteip=100.64.0.0/10"]]
    for i, cmd in enumerate(cmds):
        code, out = c.run(cmd)
        if i >= 2 and code != 0:
            return "warn", "تعذّر ضبط الجدار الناري: شغّل deploy\\open_firewall.bat كمسؤول."
    return "ok", "المنفذ %d مفتوح لشبكة المكتب وTailscale فقط (ليس للإنترنت). تأكد أن الشبكة «Private»." % p


def step_network(c):
    if not c.a.set_private:
        return "skip", "لم يُطلب"
    return lan.host_network_profile(c, ps, ps_quote)


def step_lanfile(c):
    return lan.host_alias_and_export(c)


def step_autostart(c):
    if not c.a.autostart:
        return "skip", "لم يُطلب"
    code, out = c.run(ps("& " + ps_quote(os.path.join(c.a.app, "deploy", "install_autostart.ps1"))))
    if code != 0:
        return "warn", "تعذّر إنشاء التشغيل التلقائي: شغّل deploy\\install_autostart.bat كمسؤول."
    return "ok", "التشغيل التلقائي: يبدأ بعد دخولك إلى ويندوز بـ30 ثانية (بحساب %s)" % os.environ.get("USERNAME", "")


def step_start(c):
    if not c.a.start_after:
        return "skip", "لم يُطلب"
    a = c.a
    if c.dry:
        return "ok", "سيُشغَّل الخادم ويُفحص /health"
    if not port_free(a.port):
        return "warn", "المنفذ %d مشغول (ربما النظام يعمل بالفعل)." % a.port
    os.makedirs(a.logs, exist_ok=True)
    out = open(os.path.join(a.logs, "server.out.txt"), "ab")
    flags = (0x00000008 | 0x00000200) if IS_WINDOWS else 0                      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([c.python, "serve.py"], cwd=a.app, stdout=out, stderr=out, env=dict(os.environ, PYTHONUTF8="1"), creationflags=flags)
    url = "http://127.0.0.1:%d/health" % int(a.port)
    for _ in range(60):
        time.sleep(1)
        try:
            with urllib.request.urlopen(url, timeout=3) as r:
                body = r.read().decode("utf-8", "replace")
                c.log(body[:300])
                return ("ok", "الخادم يعمل: %s (health: %s)" % (url, "ok" if '"ok"' in body or "'ok'" in body else "انظر السجل"))
        except Exception:                                # noqa
            continue
    return "warn", "لم يستجب الخادم خلال دقيقة: راجع %s\\server.out.txt" % a.logs


STEPS = [("preflight", "فحص الجهاز", None), ("python", "بايثون 3.12 (مع PATH)", step_python), ("ace", "سائق Access (ACE)", step_ace), ("tesseract", "Tesseract العربي", step_tesseract),
         ("folders", "إنشاء المجلدات", step_folders), ("share", "مشاركة مجلد الشير", step_share), ("unpack", "فك البرنامج", step_unpack),
         ("database", "قاعدة البيانات", step_database), ("config", "ملف config.ini", step_config), ("pip", "تثبيت المكتبات (pip)", step_pip),
         ("migrate", "تهيئة القاعدة (migrate)", step_migrate), ("checks", "فحص الجاهزية", step_checks), ("network", "نوع الشبكة (Private)", step_network), ("firewall", "الجدار الناري", step_firewall), ("lanfile", "اسم الرابط وملف الفريق", step_lanfile),
         ("autostart", "التشغيل التلقائي", step_autostart), ("start", "تشغيل الخادم", step_start)]
# a failed step stops everything after it, except these (the system is usable without them)
OPTIONAL = {"tesseract", "share", "network", "firewall", "lanfile", "autostart", "start", "checks"}


def step_preflight(c):
    a = c.a
    problems = validate(a, check_paths=not c.dry)
    if problems:
        return "fail", "؛ ".join(problems)
    if IS_WINDOWS and not is_admin() and not c.dry:
        return "fail", "شغّل الأداة كمسؤول (Run as administrator)."
    if IS_WINDOWS:
        if sys.maxsize <= 2 ** 32:
            return "fail", "يلزم ويندوز 64-bit."
        drive = os.path.splitdrive(a.base)[0] + "\\"
        try:
            free = shutil.disk_usage(drive).free
        except OSError:
            return "fail", "القرص %s غير موجود." % drive
        if free < 3 * 1024 ** 3:
            return "fail", "المساحة الحرة على %s أقل من 3 جيجابايت." % drive
    if not port_free(a.port) and not c.dry:
        c.notes.append("المنفذ %d مشغول حالياً (ربما نسخة تعمل)." % a.port)
    return "ok", "الجهاز مناسب"


def run_install(answers, log, progress=None, dry=False, run=None, download=None, only=None):
    """Runs the steps in order. Returns (results list [(key, title, status, detail)], overall 'ok'|'warn'|'fail')."""
    ctx = Ctx(answers, log=log, dry=dry, run=run, download=download)
    results, overall, stopped = [], "ok", False
    for key, title, fn in STEPS:
        if only and key not in only:
            continue
        if stopped:
            results.append((key, title, "skip", "لم يُنفَّذ بسبب فشل خطوة سابقة"))
            continue
        fn = fn or step_preflight
        if progress:
            progress(key, title, "running", "")
        try:
            status, detail = fn(ctx)
        except Exception as e:                           # noqa - never a raw traceback for the person; it goes to the log
            ctx.log("EXCEPTION in %s: %r" % (key, e))
            status, detail = "fail", "خطأ غير متوقع: %s" % e
        ctx.log("[%s] %s - %s" % (status.upper(), title, detail))
        results.append((key, title, status, detail))
        if progress:
            progress(key, title, status, detail)
        if status == "fail":
            if key in OPTIONAL:
                overall = "warn" if overall == "ok" else overall
            else:
                overall, stopped = "fail", True
        elif status == "warn" and overall == "ok":
            overall = "warn"
    return results, overall, ctx


def make_logger(path):
    def log(msg):
        line = "%s  %s" % (datetime.now().strftime("%H:%M:%S"), msg)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass
    return log


def summary_text(results, overall, a):
    icon = {"ok": "✔", "warn": "⚠", "fail": "✘", "skip": "–", "running": "…"}
    lines = ["النتيجة: " + {"ok": "اكتمل التثبيت بنجاح", "warn": "اكتمل مع ملاحظات (اقرأها)", "fail": "توقّف التثبيت (انظر الخطوة التي فشلت)"}[overall], ""]
    for key, title, status, detail in results:
        lines.append("%s %s — %s" % (icon.get(status, "?"), title, detail))
    if overall != "fail":
        lines += ["", "رابط النظام لأجهزة الفريق:  http://%s:%d   (أو بالعنوان الرقمي من ملف client_import.json)" % ((a.alias_name or socket.gethostname()), int(a.port)),
                  "مجلد البرنامج: %s   |   الأرشيف المشارك: %s   |   السجل: %s" % (a.app, a.share_folder, a.log_file),
                  "التالي: ادخل بحساب المدير وأضف بقية الفريق (python po.bat tools\\add_user.py)، وضع backup_archive_dir في config.ini وشغّل deploy\\backup_all.bat مرة يدوياً."]
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------------------- the wizard (tkinter)
def wizard(initial=None, selftest_out=None):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    import threading
    root = tk.Tk()
    root.title("تثبيت نظام المشتريات والمقاولين")
    root.geometry("760x640")
    a = initial or Answers()
    v = {k: tk.StringVar(value=str(getattr(a, k, ""))) for k in ("share_folder", "base", "share_name", "team_group", "db_existing", "admin_user", "admin_name", "admin_password", "admin_password2",
                                                              "backup_db_dir", "backup_set_dir", "port", "allowed_extra", "alias_name", "export_dir", "restore_package")}
    flags = {k: tk.BooleanVar(value=getattr(a, k)) for k in ("create_share", "install_tesseract", "open_firewall", "autostart", "start_after", "replace_existing", "set_private")}
    db_mode = tk.StringVar(value=a.db_mode)
    frames = {}
    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True, padx=8, pady=8)

    def page(title):
        f = ttk.Frame(nb, padding=12)
        nb.add(f, text=title)
        return f

    def row(f, label, var, r, browse=False, show=None, folder=True):
        ttk.Label(f, text=label, wraplength=300, justify="right").grid(row=r, column=2, sticky="e", pady=4)
        e = ttk.Entry(f, textvariable=var, width=46, show=show or "")
        e.grid(row=r, column=1, sticky="ew", padx=6)
        if browse:
            ttk.Button(f, text="استعراض…", command=lambda: var.set((filedialog.askdirectory() if folder else filedialog.askopenfilename()) or var.get())).grid(row=r, column=0)
        return e

    f1 = page("١) مجلد الشير")
    ttk.Label(f1, text="اختر أولاً مجلد الشير (الأرشيف المشترك) الذي ستخزَّن فيه كل الملفات.\nباقي المجلدات تُقترح تلقائياً على القرص نفسه.", justify="right").grid(row=0, column=0, columnspan=3, sticky="e")
    drives = ", ".join("%s (حر %.0f جيجا)" % (d, free / 1024 ** 3) for d, free, _t in fixed_drives())
    ttk.Label(f1, text="الأقراص المتاحة: " + (drives or "—")).grid(row=1, column=0, columnspan=3, sticky="e")
    row(f1, "مجلد الشير (الأرشيف):", v["share_folder"], 2, browse=True)
    row(f1, "اسم المشاركة على الشبكة:", v["share_name"], 3)
    row(f1, "مجلد التثبيت (البرنامج والبيانات):", v["base"], 4, browse=True)
    ttk.Checkbutton(f1, text="أنشئ المشاركة تلقائياً (الفريق: قراءة فقط)", variable=flags["create_share"]).grid(row=5, column=1, sticky="e")
    row(f1, "مجموعة الفريق (قراءة فقط):", v["team_group"], 6)

    def suggest():
        d = derive_defaults(v["share_folder"].get())
        v["base"].set(d.base)
        v["share_name"].set(d.share_name)
    ttk.Button(f1, text="اقترح باقي المسارات من مجلد الشير", command=suggest).grid(row=7, column=1, sticky="e", pady=8)

    f2 = page("٢) القاعدة والنسخ")
    ttk.Radiobutton(f2, text="قاعدة جديدة فارغة (تثبيت جديد)", variable=db_mode, value="new").grid(row=0, column=1, sticky="e")
    ttk.Radiobutton(f2, text="استعمال نسخة من قاعدة موجودة (يُنسخ ملفها ولا يُلمس الأصل)", variable=db_mode, value="existing").grid(row=1, column=1, sticky="e")
    row(f2, "ملف القاعدة الموجودة:", v["db_existing"], 2, browse=True, folder=False)
    ttk.Radiobutton(f2, text="استرداد من حزمة تصدير (PO_Export_….zip): القاعدة + كل الملفات من الجهاز القديم", variable=db_mode, value="restore").grid(row=3, column=1, sticky="e")
    row(f2, "ملف حزمة التصدير:", v["restore_package"], 4, browse=True, folder=False)
    row(f2, "مجلد نسخ القاعدة (قرص آخر يُفضَّل):", v["backup_db_dir"], 5, browse=True)
    row(f2, "مجلد نسخ القاعدة + الأرشيف معاً (قرص آخر):", v["backup_set_dir"], 6, browse=True)
    row(f2, "منفذ النظام:", v["port"], 7)

    f3 = page("٣) حساب المدير")
    ttk.Label(f3, text="مطلوب للقاعدة الجديدة. للقاعدة الموجودة أو المستردّة: اتركه فارغاً (الحسابات تأتي مع القاعدة) إلا إذا كانت بلا حساب مدير. كلمة المرور لا تُحفظ ولا تُكتب في السجل.").grid(row=0, column=0, columnspan=3, sticky="e")
    row(f3, "اسم المستخدم (إنجليزي):", v["admin_user"], 1)
    row(f3, "الاسم الظاهر:", v["admin_name"], 2)
    row(f3, "كلمة المرور (8+ وحرف ورقم):", v["admin_password"], 3, show="•")
    row(f3, "تأكيد كلمة المرور:", v["admin_password2"], 4, show="•")

    f4 = page("٤) خيارات")
    for i, (k, t) in enumerate((("install_tesseract", "ثبّت Tesseract مع العربية (قراءة صور عروض الأسعار)"), ("open_firewall", "افتح المنفذ لشبكة المكتب وTailscale فقط"),
                                ("autostart", "شغّل النظام تلقائياً بعد دخول ويندوز"), ("start_after", "شغّل الخادم بعد التثبيت"), ("replace_existing", "استبدل برنامجاً قديماً موجوداً (مع حفظ config.ini وinstance)"),
                                ("set_private", "حوّل الشبكة إلى Private إن كانت Public (بدونه يحجب الجدار الناري أجهزة الفريق)"))):
        ttk.Checkbutton(f4, text=t, variable=flags[k]).grid(row=i, column=0, columnspan=3, sticky="e", pady=4)
    row(f4, "اسم بديل للرابط (مثل poweb، أو اتركه فارغاً):", v["alias_name"], 8)
    row(f4, "نطاقات إضافية للمكتب (اختياري، مثل 192.168.5.0/24):", v["allowed_extra"], 9)
    row(f4, "مجلد حفظ ملف الفريق client_import.json (فارغ = مجلد التثبيت):", v["export_dir"], 10, browse=True)

    f5 = page("٥) التثبيت")
    box = tk.Text(f5, height=26, width=92, wrap="word")
    box.pack(fill="both", expand=True)
    btn = ttk.Button(f5, text="ابدأ التثبيت")
    btn.pack(pady=6)

    def collect():
        z = Answers()
        for k in ("share_folder", "base", "share_name", "team_group", "db_existing", "admin_user", "admin_name", "admin_password", "backup_db_dir", "backup_set_dir", "allowed_extra", "alias_name", "export_dir", "restore_package"):
            setattr(z, k, v[k].get().strip() if k != "admin_password" else v[k].get())
        z.port = int(v["port"].get() or 5000)
        z.db_mode = db_mode.get()
        for k, var in flags.items():
            setattr(z, k, bool(var.get()))
        return z

    def say(t):
        box.insert("end", t + "\n")
        box.see("end")

    def go():
        try:
            z = collect()
        except ValueError:
            messagebox.showerror("خطأ", "رقم المنفذ غير صالح.")
            return
        if v["admin_password"].get() != v["admin_password2"].get():
            messagebox.showerror("خطأ", "كلمتا المرور غير متطابقتين.")
            return
        errs = validate(z)
        if errs:
            messagebox.showerror("راجع المدخلات", "\n".join(errs))
            return
        if not messagebox.askyesno("تأكيد", "سيُثبَّت النظام في:\n%s\nوالأرشيف المشترك في:\n%s\n\nمتابعة؟" % (z.app, z.share_folder)):
            return
        btn.state(["disabled"])
        nb.select(f5)

        def work():
            res, overall, _c = run_install(z, make_logger(z.log_file), progress=lambda k, t, s, d: root.after(0, say, "%s %s %s" % ({"running": "…", "ok": "✔", "warn": "⚠", "fail": "✘", "skip": "–"}[s], t, d)))
            root.after(0, say, "\n" + summary_text(res, overall, z))
            root.after(0, lambda: btn.state(["!disabled"]))
        threading.Thread(target=work, daemon=True).start()
    btn.configure(command=go)
    if selftest_out:                       # build everything above for real, read the fields back, validate, close (used by CI on Windows)
        problems = []
        try:
            root.update()
            z = collect()
            if z.port != int(a.port) or z.share_folder != a.share_folder:
                problems.append("collect() does not return what was shown")
            validate(z, check_paths=False)
            if len(nb.tabs()) != 5:
                problems.append("tabs=%d" % len(nb.tabs()))
            suggest()
        except Exception as e:             # noqa: BLE001
            problems.append("%s: %s" % (type(e).__name__, e))
        root.destroy()
        text = "OK" if not problems else "PROBLEMS: " + "; ".join(problems)
        open(selftest_out, "w", encoding="utf-8").write(text + "\n")
        print(text)
        return 0 if not problems else 1
    root.mainloop()


# ------------------------------------------------------------------------------------------------------------- the client (team PC) window + the first screen
def client_wizard(initial=None, selftest_out=None):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    import threading
    root = tk.Tk()
    root.title("إعداد جهاز عضو الفريق")
    root.geometry("700x560")
    a = initial or lan.ClientAnswers()
    v = {k: tk.StringVar(value=str(getattr(a, k, ""))) for k in ("import_file", "server", "port", "alias", "tailscale_ip", "shortcut_name")}
    flags = {k: tk.BooleanVar(value=bool(getattr(a, k))) for k in ("outside_office", "shortcut", "open_browser")}
    f = ttk.Frame(root, padding=14)
    f.pack(fill="both", expand=True)
    ttk.Label(f, text="هذا الجهاز سيفتح النظام من الجهاز الرئيسي. اختر ملف client_import.json الذي أعطاك إياه مسؤول النظام، أو اكتب عنوان الجهاز الرئيسي.",
              wraplength=640, justify="right").grid(row=0, column=0, columnspan=3, sticky="e", pady=6)

    def row(label, key, r, browse=False):
        ttk.Label(f, text=label, wraplength=260, justify="right").grid(row=r, column=2, sticky="e", pady=4)
        ttk.Entry(f, textvariable=v[key], width=40).grid(row=r, column=1, sticky="ew", padx=6)
        if browse:
            ttk.Button(f, text="استعراض…", command=lambda: v[key].set(filedialog.askopenfilename(filetypes=[("JSON", "*.json")]) or v[key].get())).grid(row=r, column=0)
    row("ملف الفريق (client_import.json):", "import_file", 1, browse=True)
    row("أو عنوان الجهاز الرئيسي (مثل 192.168.100.55):", "server", 2)
    row("المنفذ:", "port", 3)
    row("اسم بديل للرابط (اختياري، مثل poweb):", "alias", 4)
    row("عنوان Tailscale (للعمل من خارج المكتب):", "tailscale_ip", 5)
    row("اسم الاختصار على سطح المكتب:", "shortcut_name", 6)
    ttk.Checkbutton(f, text="أعمل الآن من خارج المكتب (عبر Tailscale)", variable=flags["outside_office"]).grid(row=7, column=0, columnspan=3, sticky="e")
    ttk.Checkbutton(f, text="أنشئ اختصاراً على سطح المكتب", variable=flags["shortcut"]).grid(row=8, column=0, columnspan=3, sticky="e")
    ttk.Checkbutton(f, text="افتح النظام بعد الإعداد", variable=flags["open_browser"]).grid(row=9, column=0, columnspan=3, sticky="e")
    box = tk.Text(f, height=10, width=80, wrap="word")
    box.grid(row=11, column=0, columnspan=3, pady=8)
    btn = ttk.Button(f, text="اختبر وأعدّ")
    btn.grid(row=10, column=1, pady=6)

    def collect():
        z = lan.ClientAnswers()
        for k in ("import_file", "server", "alias", "tailscale_ip", "shortcut_name"):
            setattr(z, k, v[k].get().strip())
        z.port = int(v["port"].get() or 5000)
        for k, var in flags.items():
            setattr(z, k, bool(var.get()))
        return z

    def go():
        try:
            z = collect()
        except ValueError:
            messagebox.showerror("خطأ", "رقم المنفذ غير صالح.")
            return
        btn.state(["disabled"])

        def work():
            res, overall = run_client_answers(z, make_logger(os.path.join(lan.program_data_dir(), "client_log.txt")))
            root.after(0, lambda: (box.insert("end", client_summary(res, overall)), btn.state(["!disabled"])))
        threading.Thread(target=work, daemon=True).start()
    btn.configure(command=go)
    if selftest_out:
        problems = []
        try:
            root.update()
            z = collect()
            if z.port != int(a.port):
                problems.append("client collect() does not return what was shown")
        except Exception as e:                       # noqa: BLE001
            problems.append("%s: %s" % (type(e).__name__, e))
        root.destroy()
        text = "OK" if not problems else "PROBLEMS: " + "; ".join(problems)
        open(selftest_out, "w", encoding="utf-8").write(text + "\n")
        print(text)
        return 0 if not problems else 1
    root.mainloop()


def run_client_answers(z, log, dry=False):
    return lan.run_client(z, log, ps, ps_quote, is_admin(), dry=dry)


def client_summary(results, overall):
    icon = {"ok": "✔", "warn": "⚠", "fail": "✘"}
    head = {"ok": "اكتمل الإعداد — افتح النظام من الاختصار", "warn": "اكتمل مع ملاحظات", "fail": "لم يكتمل الإعداد"}[overall]
    return "\n".join([head, ""] + ["%s %s — %s" % (icon.get(s, "?"), t, d) for _k, t, s, d in results])


def export_wizard(selftest_out=None):
    import tkinter as tk
    from tkinter import filedialog, ttk
    import threading
    root = tk.Tk()
    root.title("نسخ احتياطي كامل وتصدير")
    root.geometry("720x520")
    f = ttk.Frame(root, padding=14)
    f.pack(fill="both", expand=True)
    ttk.Label(f, text="يصنع حزمة واحدة (PO_Export_….zip) فيها القاعدة وكل ملفات الأرشيف والمرفقات القديمة. لا يغيّر شيئاً في النظام.\n"
                      "أوقف الخادم أولاً. للاسترداد: شغّل PO_Setup على الجهاز الجديد ← الجهاز الرئيسي ← «استرداد من حزمة تصدير».", wraplength=660, justify="right").grid(row=0, column=0, columnspan=3, sticky="e", pady=6)
    v_app, v_dest = tk.StringVar(), tk.StringVar()
    for r, (label, var) in enumerate((("مجلد البرنامج (app) الذي فيه config.ini:", v_app), ("مجلد حفظ الحزمة (قرص خارجي يُفضَّل):", v_dest)), start=1):
        ttk.Label(f, text=label, wraplength=260, justify="right").grid(row=r, column=2, sticky="e", pady=4)
        ttk.Entry(f, textvariable=var, width=46).grid(row=r, column=1, sticky="ew", padx=6)
        ttk.Button(f, text="استعراض…", command=lambda var=var: var.set(filedialog.askdirectory() or var.get())).grid(row=r, column=0)
    box = tk.Text(f, height=16, width=84, wrap="word")
    box.grid(row=4, column=0, columnspan=3, pady=8)
    btn = ttk.Button(f, text="ابدأ التصدير")
    btn.grid(row=3, column=1, pady=6)

    def say(t):
        box.insert("end", t + "\n")
        box.see("end")

    def go():
        btn.state(["disabled"])

        def work():
            text = export_text(v_app.get().strip(), v_dest.get().strip(), progress=lambda i, n: root.after(0, say, "… %d من %d ملف" % (i, n)) if i % 500 == 0 else None)
            root.after(0, lambda: (say(text), btn.state(["!disabled"])))
        threading.Thread(target=work, daemon=True).start()
    btn.configure(command=go)
    if selftest_out:
        root.update()
        root.destroy()
        open(selftest_out, "w", encoding="utf-8").write("OK\n")
        return 0
    root.mainloop()


def export_text(app_dir, dest_dir, progress=None):
    """Runs the export and returns what to show the person."""
    if not app_dir or not dest_dir:
        return "✘ اختر مجلد البرنامج ومجلد الحفظ."
    try:
        r = transfer.export_package(app_dir, dest_dir, progress=progress)
    except transfer.TransferError as e:
        return "✘ " + str(e)
    except OSError as e:
        return "✘ تعذّر التصدير: %s" % e
    c = r["counts"]
    return ("✔ اكتمل التصدير وفُحصت الحزمة ملفاً ملفاً.\nالحزمة: %s\nالحجم: %.1f ميجابايت · القاعدة: %d · ملفات الأرشيف: %d · المرفقات القديمة: %d · ملفات داخلية: %d\n"
            "بصمة الحزمة (SHA-256): %s\nانسخ هذا الملف إلى الجهاز الجديد، ثم: PO_Setup ← الجهاز الرئيسي ← «استرداد من حزمة تصدير»."
            % (r["package"], r["bytes"] / (1 << 20), c.get("db", 0), c.get("archive", 0), c.get("legacy", 0), c.get("instance", 0), r["package_sha256"]))


def launcher(selftest_out=None):
    """The first screen of the one tool: what is this PC?"""
    import tkinter as tk
    from tkinter import ttk, messagebox
    root = tk.Tk()
    root.title("نظام المشتريات والمقاولين — الإعداد")
    root.geometry("600x430")
    f = ttk.Frame(root, padding=20)
    f.pack(fill="both", expand=True)
    ttk.Label(f, text="ما هو دور هذا الجهاز؟", font=("", 14, "bold")).pack(pady=10)
    choice = {"role": None}

    def pick(role):
        choice["role"] = role
        root.destroy()
    ttk.Button(f, text="الجهاز الرئيسي (يحمل القاعدة والأرشيف — تثبيت كامل)", command=lambda: pick("host")).pack(fill="x", pady=6, ipady=8)
    ttk.Button(f, text="جهاز عضو في الفريق (يفتح النظام من الجهاز الرئيسي)", command=lambda: pick("client")).pack(fill="x", pady=6, ipady=8)
    ttk.Button(f, text="نسخ احتياطي كامل وتصدير (القاعدة + كل الملفات في حزمة واحدة)", command=lambda: pick("export")).pack(fill="x", pady=6, ipady=8)
    ttk.Button(f, text="إزالة إعدادات الشبكة التي أضافتها الأداة (لا تمس البرنامج ولا البيانات)", command=lambda: pick("remove")).pack(fill="x", pady=6, ipady=8)
    if selftest_out:
        root.update()
        root.destroy()
        open(selftest_out, "w", encoding="utf-8").write("OK\n")
        return None
    root.mainloop()
    return choice["role"]


def _run_plain(cmd):
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
        return p.returncode, (p.stdout or b"").decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def remove_lan_cli(log=print):
    lines = lan.remove_lan(_run_plain, ps)
    for l in lines:
        log(l)
    return lines


# ------------------------------------------------------------------------------------------------------------- main
def relaunch_as_admin():
    params = " ".join('"%s"' % x for x in sys.argv[1:])
    exe = sys.executable
    args = ('"%s" %s' % (os.path.abspath(__file__), params)) if not getattr(sys, "frozen", False) else params
    ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, args, None, 1)


def fix_console():
    """Arabic text must never crash the tool: UTF-8 output, and no console at all (a windowed exe has sys.stdout = None) is fine."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
        else:
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass


def main(argv=None):
    fix_console()
    ap = argparse.ArgumentParser(description="PO Setup")
    ap.add_argument("--silent", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--answers", help="JSON file of answers")
    ap.add_argument("--selftest", action="store_true", help="checks that this tool's own logic works (no changes), writes the result to --out")
    ap.add_argument("--gui-selftest", action="store_true", help="opens the wizard window logic, checks it, closes it (writes the result to --out)")
    ap.add_argument("--role", choices=("host", "client"), help="host = main PC (full installation); client = a team PC (no program, only the way to open it)")
    ap.add_argument("--import-file", help="client: client_import.json made by the host")
    ap.add_argument("--server", help="client: address of the main PC")
    ap.add_argument("--port", type=int, help="client: port (default 5000)")
    ap.add_argument("--alias", help="client: friendly name for the link")
    ap.add_argument("--export", action="store_true", help="full backup: writes PO_Export_<date>.zip (database + archive + old uploads) from --app into --dest; changes nothing")
    ap.add_argument("--app", help="export: the program folder (the one with config.ini)")
    ap.add_argument("--dest", help="export: where the package is written")
    ap.add_argument("--verify-package", help="checks every file of a PO_Export package against its SHA-256")
    ap.add_argument("--remove-lan", action="store_true", help="removes what this tool added to the network settings (firewall rules, hosts lines, shortcuts); never touches program or data")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    a = Answers()
    if args.answers:
        data = json.load(open(args.answers, encoding="utf-8-sig"))
        for k, val in data.items():
            if hasattr(a, k):
                setattr(a, k, val)
    if args.selftest:
        problems = []
        d = derive_defaults(r"E:\Share")
        if not d.base.startswith("E:"):
            problems.append("derive_defaults")
        if "[database]" not in build_config(d):
            problems.append("build_config")
        res, overall, _ = run_install(Answers(share_folder=r"E:\Share", base=r"E:\PO", admin_password="Passw0rd1", zip_path=find_zip(a) or "x.zip"), lambda m: None, dry=True, only={"folders", "config"})
        if overall == "fail" and any(r[2] == "fail" and r[0] not in ("unpack",) for r in res):
            problems.append("dry-run: " + str(res))
        text = "OK" if not problems else "PROBLEMS: " + "; ".join(problems)
        if args.out:
            open(args.out, "w", encoding="utf-8").write(text + "\nzip=%s\nwindows=%s\nadmin=%s\n" % (find_zip(a), IS_WINDOWS, is_admin()))
        print(text)
        return 0 if not problems else 1
    if args.remove_lan:
        remove_lan_cli()
        return 0
    if args.export:
        text = export_text(args.app or "", args.dest or "")
        print(text)
        return 0 if text.startswith("✔") else 1
    if args.verify_package:
        try:
            r = transfer.verify_package(args.verify_package)
        except transfer.TransferError as e:
            print("✘ " + str(e))
            return 1
        print(("✔ الحزمة سليمة: %d ملف" % r["files"]) if r["ok"] else "✘ مشاكل: " + "؛ ".join(r["problems"][:10]))
        return 0 if r["ok"] else 1
    if args.role == "client" and (args.silent or args.dry_run):
        z = lan.ClientAnswers(import_file=args.import_file or "", server=args.server or "", alias=args.alias or "", open_browser=False)
        if args.port:
            z.port = args.port
        res, overall = run_client_answers(z, print, dry=args.dry_run)
        print(client_summary(res, overall))
        return 0 if overall in ("ok", "warn") else 1
    if args.dry_run or args.silent:
        errs = validate(a, check_paths=False)
        if errs:
            print("\n".join(errs))
            return 2
        res, overall, _c = run_install(a, make_logger(a.log_file) if not args.dry_run else print, dry=args.dry_run)
        print(summary_text(res, overall, a))
        return 0 if overall in ("ok", "warn") else 1
    if args.gui_selftest:
        out = args.out or os.path.join(os.environ.get("TEMP", "."), "po_gui_selftest.txt")
        parts = []
        rc = 0
        for name, fn in (("launcher", lambda o: launcher(selftest_out=o) or 0), ("host", lambda o: wizard(a, selftest_out=o)), ("client", lambda o: client_wizard(None, selftest_out=o)), ("export", lambda o: export_wizard(selftest_out=o))):
            o = out + "." + name
            try:
                rc |= int(fn(o) or 0)
                parts.append("%s: %s" % (name, open(o, encoding="utf-8").read().strip()))
            except Exception as e:                             # noqa: BLE001
                rc = 1
                parts.append("%s: PROBLEMS %s: %s" % (name, type(e).__name__, e))
        text = ("OK" if rc == 0 else "PROBLEMS") + "\n" + "\n".join(parts) + "\n"
        open(out, "w", encoding="utf-8").write(text)
        print(text)
        return rc
    if IS_WINDOWS and not is_admin():
        relaunch_as_admin()
        return 0
    role = args.role
    if not role:
        role = launcher()
        if role is None:
            return 0
    if role == "remove":
        remove_lan_cli()
        return 0
    if role == "export":
        export_wizard()
        return 0
    if role == "client":
        client_wizard(lan.ClientAnswers(import_file=args.import_file or "", server=args.server or "", alias=args.alias or ""))
        return 0
    wizard(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())

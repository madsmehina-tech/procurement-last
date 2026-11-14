# -*- coding: utf-8 -*-
"""
PO Setup - the ADMIN panel logic: everything a person had to do by editing config.ini or typing commands, as functions the window calls.

  * backup folders (three kinds) -> written into config.ini (a dated copy of config.ini is kept first)
  * backup now, readiness checks, where-is-my-data: run the system's own tools and return their text
  * the server: status / clean stop / start / restart (through the scheduled task when there is one)
  * a new system account (user + role) without the console
Nothing here deletes data. Every change of config.ini keeps `config.ini.bak_<date>` next to it.
"""
import configparser
import json
import os
import re
import shutil
import socket
import subprocess
from datetime import datetime

BOOT_TASK = "PO Web Server (Boot)"
LOGON_TASK = "PO Web Server"

BACKUP_KEYS = [  # key in [storage], title, explanation
    ("backup_dir", "نسخ القاعدة الدورية",
     "نسخة من ملف القاعدة فقط، تؤخذ تلقائياً كل عدة ساعات والخادم يعمل. سريعة وصغيرة. تحميك من خطأ إدخال أو تلف الملف."),
    ("backup_extra_dir", "مكان ثانٍ لنسخ القاعدة (اختياري)",
     "نسخة إضافية من القاعدة في مكان آخر: قرص ثانٍ أو مجلد على جهاز آخر أو OneDrive. تحميك إذا تعطل القرص الأول."),
    ("backup_archive_dir", "النسخة الكاملة: القاعدة + كل الملفات",
     "القاعدة ومعها كل ملفات الأرشيف (PDF وغيرها) وقائمة ببصمات الملفات. يجب أن تكون على قرص غير قرص البيانات. أول مرة تنسخ كل شيء، وبعدها الجديد فقط، ولا يُحذف منها شيء."),
]


class AdminError(Exception):
    pass


# ------------------------------------------------------------------------------------------------------------- config.ini
def read_ini(app):
    ini = os.path.join(app, "config.ini")
    if not os.path.isfile(ini):
        raise AdminError("لم أجد config.ini في %s — اختر مجلد البرنامج (الذي فيه serve.py)." % app)
    cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";",))
    with open(ini, "r", encoding="utf-8-sig") as f:
        cp.read_file(f)
    return ini, cp


def settings(app):
    """What the panel shows: the data paths, the three backup folders, the port."""
    ini, cp = read_ini(app)

    def raw(section, key, default=""):
        try:
            return os.path.expandvars(cp.get(section, key).strip().strip('"'))
        except (configparser.Error, AttributeError):
            return default

    def path(section, key, default=""):
        v = raw(section, key, default)
        return (v if os.path.isabs(v) else os.path.normpath(os.path.join(app, v))) if v else ""
    try:
        port = int(raw("server", "port", "5000") or 5000)
    except ValueError:
        port = 5000
    return {"ini": ini, "db": path("database", "path", r"C:\PurchaseSystem.accdb"), "archive": path("storage", "archive_root", "archive"),
            "legacy": path("storage", "legacy_uploads", "uploads"), "backup_dir": path("storage", "backup_dir", "backups"),
            "backup_extra_dir": path("storage", "backup_extra_dir"), "backup_archive_dir": path("storage", "backup_archive_dir"),
            "backup_keep": raw("storage", "backup_keep", "20"), "backup_auto_hours": raw("storage", "backup_auto_hours", "12"), "port": port}


def set_ini_values(ini, section, updates, now=None):
    """Sets key = value lines inside [section], keeping every other line and comment as it is. An empty value removes the key.
    A dated copy of the file is kept first. Returns the path of that copy."""
    with open(ini, "r", encoding="utf-8-sig") as f:
        lines = f.read().splitlines()
    bak = "%s.bak_%s" % (ini, (now or datetime.now()).strftime("%Y%m%d_%H%M%S"))
    shutil.copy2(ini, bak)
    head = re.compile(r"^\s*\[(.+?)\]\s*$")
    start = end = None
    for i, l in enumerate(lines):
        m = head.match(l)
        if m:
            if start is not None and end is None:
                end = i
            if m.group(1).strip().lower() == section.lower() and start is None:
                start = i
    if start is None:
        lines += ["", "[%s]" % section]
        start, end = len(lines) - 1, len(lines)
    if end is None:
        end = len(lines)
    body = lines[start + 1:end]
    left = dict(updates)
    out = []
    for l in body:
        m = re.match(r"^\s*([A-Za-z0-9_]+)\s*=", l)
        if m and m.group(1).lower() in {k.lower() for k in left}:
            key = next(k for k in left if k.lower() == m.group(1).lower())
            val = left.pop(key)
            if val != "":
                out.append("%s = %s" % (key, val))
            continue
        out.append(l)
    while out and not out[-1].strip():
        out.pop()
    for key, val in left.items():
        if val != "":
            out.append("%s = %s" % (key, val))
    out.append("")
    lines[start + 1:end] = out
    with open(ini, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(lines).rstrip("\r\n") + "\r\n")
    return bak


def _norm(p):
    return os.path.normcase(os.path.abspath(p))


def _inside(child, parent):
    c, p = _norm(child), _norm(parent)
    return c == p or c.startswith(p.rstrip("\\/") + os.sep)


def _drive(p):
    return os.path.splitdrive(os.path.abspath(p))[0].lower() or "/"


def check_backup_folders(app, values):
    """(errors, warnings) for the three backup folders. Errors block saving."""
    s = settings(app)
    errs, warns = [], []
    for key, title, _why in BACKUP_KEYS:
        v = (values.get(key) or "").strip()
        if not v:
            if key == "backup_dir":
                errs.append("«%s»: اختر مجلداً." % title)
            continue
        if not os.path.isabs(v) and not re.match(r"^\\\\", v):
            errs.append("«%s»: اختر مجلداً بمسار كامل." % title)
            continue
        for label, prot in (("مجلد الأرشيف", s["archive"]), ("مجلد البرنامج", app), ("مجلد المرفقات القديمة", s["legacy"])):
            if prot and _inside(v, prot):
                errs.append("«%s»: لا يكون داخل %s (%s)." % (title, label, prot))
        if key == "backup_archive_dir":
            if _inside(v, os.path.dirname(s["db"])) or _inside(s["archive"], v) or _inside(s["db"], v):
                errs.append("«%s»: اختر مجلداً مستقلاً عن مجلد القاعدة والأرشيف." % title)
            elif _drive(v) == _drive(s["db"]) or _drive(v) == _drive(s["archive"]):
                warns.append("«%s» على القرص نفسه الذي فيه البيانات (%s): لن يحميك إذا تعطل هذا القرص. الأفضل قرص آخر أو خارجي." % (title, _drive(v).upper()))
        if key == "backup_extra_dir" and _drive(v) == _drive(s["db"]):
            warns.append("«%s» على قرص القاعدة نفسه: الفائدة منه قليلة." % title)
    picked = [_norm(values[k]) for k, _t, _w in BACKUP_KEYS if (values.get(k) or "").strip()]
    if len(set(picked)) != len(picked):
        errs.append("اختر مجلداً مختلفاً لكل نوع من النسخ.")
    return errs, warns


def save_backup_folders(app, values, keep=None, hours=None):
    """Checks, creates the folders, writes config.ini. Returns (config backup path, warnings). Raises AdminError with the reasons."""
    errs, warns = check_backup_folders(app, values)
    for name, val, lo, hi in (("عدد النسخ المحفوظة", keep, 1, 500), ("كل كم ساعة", hours, 0, 168)):
        if val not in (None, "") and not (str(val).isdigit() and lo <= int(val) <= hi):
            errs.append("%s: رقم بين %d و%d." % (name, lo, hi))
    if errs:
        raise AdminError("\n".join(errs))
    updates = {}
    for key, _t, _w in BACKUP_KEYS:
        v = (values.get(key) or "").strip()
        updates[key] = v
        if v:
            try:
                os.makedirs(v, exist_ok=True)
            except OSError as e:
                raise AdminError("تعذّر إنشاء المجلد %s (%s)." % (v, e))
    if keep not in (None, ""):
        updates["backup_keep"] = str(int(keep))
    if hours not in (None, ""):
        updates["backup_auto_hours"] = str(int(hours))
    ini = settings(app)["ini"]
    return set_ini_values(ini, "storage", updates), warns


# ------------------------------------------------------------------------------------------------------------- running the system's tools
def find_python(app):
    p = os.path.join(app, "deploy", "python_path.txt")
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            v = f.read().strip()
        if v:
            return v
    except OSError:
        pass
    return "python"


def run_tool(app, args, env=None, timeout=3600, runner=None):
    """Runs `python <args>` of the installation in its program folder. Returns (exit code, text)."""
    cmd = [find_python(app)] + list(args)
    e = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    e.update(env or {})
    if runner:
        return runner(cmd, app, e)
    try:
        p = subprocess.run(cmd, cwd=app, env=e, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
                           creationflags=0x08000000 if os.name == "nt" else 0)                 # no console window
    except (OSError, subprocess.TimeoutExpired) as ex:
        return 1, "%s: %s" % (type(ex).__name__, ex)
    return p.returncode, ((p.stdout or b"") + (p.stderr or b"")).decode("utf-8", "replace")


def backup_now(app, runner=None):
    s = settings(app)
    if not s["backup_archive_dir"]:
        raise AdminError("لم يُحدَّد مجلد «النسخة الكاملة» بعد. اختره واحفظ الإعدادات أولاً.")
    return run_tool(app, [os.path.join("tools", "backup_all.py")], runner=runner)



def _excel(path):
    path = (path or "").strip().strip('"')
    if not path or not os.path.isfile(path):
        raise AdminError("اختر ملف الإكسل القديم أولاً (PO_Tracker.xlsm).")
    return path


def excel_orders(app, xlsx, apply=False, only="", runner=None):
    """Preview (nothing is written) or apply the completion of the orders from the old Excel tracker. Before applying, a dated copy of the database is taken; no copy = no import."""
    args = [os.path.join("tools", "import_excel_orders.py"), _excel(xlsx)]
    if (only or "").strip():
        args += ["--only", only.strip()]
    if not apply:
        return run_tool(app, args, timeout=900, runner=runner)
    code, out = run_tool(app, [os.path.join("tools", "backup_now.py")], timeout=1800, runner=runner)
    if code != 0:
        return code, "لم تُؤخذ نسخة احتياطية، فلم يبدأ النقل:\n" + out
    code2, out2 = run_tool(app, args + ["--apply", "--yes"], timeout=1800, runner=runner)
    return code2, out.strip() + "\n\n" + out2


def excel_pdfs(app, xlsx, search="", apply=False, runner=None):
    """Preview or apply the copy of the old tracker's PDF files into the archive (tools/import_legacy_pdfs.py). The old files are never moved or deleted."""
    args = [os.path.join("tools", "import_legacy_pdfs.py"), _excel(xlsx)]
    search = (search or "").strip().strip('"')
    if search:
        if not os.path.isdir(search):
            raise AdminError("مجلد الملفات القديمة غير موجود: %s" % search)
        args += ["--search", search]
    if apply:
        args += ["--apply", "--yes"]
    return run_tool(app, args, timeout=3600, runner=runner)


USER_CODE = r"""
import json, os, re, sys
import db
from werkzeug.security import generate_password_hash
mode = os.environ["PO_MODE"]
roles = sorted(db.query("SELECT [RoleID], [RoleName], [RoleKey] FROM [Roles]"), key=lambda r: r["RoleID"])
users = db.query("SELECT [Username], [Status], [RoleID] FROM [Users]")
if mode == "list":
    print("JSON=" + json.dumps({"roles": [[r["RoleID"], r["RoleName"]] for r in roles], "users": [[u["Username"], u["Status"]] for u in users]}, ensure_ascii=True))
    sys.exit(0)
u, pw = os.environ["PO_NEW_USER"].strip(), os.environ["PO_NEW_PASSWORD"]
if not re.match(r"^[A-Za-z0-9._-]{3,30}$", u):
    print("ERR=name"); sys.exit(2)
if any(x["Username"].lower() == u.lower() for x in users):
    print("ERR=exists"); sys.exit(2)
if len(pw) < 8 or not re.search(r"[A-Za-z]", pw) or not re.search(r"\d", pw):
    print("ERR=password"); sys.exit(2)
rid = int(os.environ["PO_NEW_ROLE"])
if rid not in [r["RoleID"] for r in roles]:
    print("ERR=role"); sys.exit(2)
db.execute("INSERT INTO [Users] ([Username], [PasswordHash], [FullName], [JobTitle], [Status], [RoleID], [Role], [CreatedAt]) VALUES (?, ?, ?, ?, 'active', ?, 'user', Now())",
           (u, generate_password_hash(pw), os.environ.get("PO_NEW_FULLNAME", "").strip() or u, os.environ.get("PO_NEW_JOB", "").strip(), rid))
print("OK=" + u)
"""
USER_ERRORS = {"name": "اسم المستخدم: حروف إنجليزية وأرقام (3 إلى 30).", "exists": "اسم المستخدم موجود بالفعل.",
               "password": "كلمة المرور: 8 خانات فأكثر وفيها حرف ورقم.", "role": "الدور غير معروف."}


def list_roles_users(app, runner=None):
    code, out = run_tool(app, ["-c", USER_CODE], env={"PO_MODE": "list"}, timeout=120, runner=runner)
    m = re.search(r"JSON=(\{.*\})", out)
    if code != 0 or not m:
        raise AdminError("تعذّر قراءة الأدوار من القاعدة: %s" % " ".join(out.split())[-200:])
    return json.loads(m.group(1))


def add_user(app, username, fullname, job, role_id, password, runner=None):
    """Creates an active system account. The password goes through the child's environment only."""
    code, out = run_tool(app, ["-c", USER_CODE], timeout=120, runner=runner,
                         env={"PO_MODE": "add", "PO_NEW_USER": username, "PO_NEW_FULLNAME": fullname, "PO_NEW_JOB": job, "PO_NEW_ROLE": str(role_id), "PO_NEW_PASSWORD": password})
    if "OK=" in out and code == 0:
        return "أُنشئ الحساب «%s» ويمكنه الدخول الآن." % username
    m = re.search(r"ERR=(\w+)", out)
    raise AdminError(USER_ERRORS.get(m.group(1), m.group(1)) if m else "تعذّر إنشاء الحساب: %s" % " ".join(out.split())[-200:])


# ------------------------------------------------------------------------------------------------------------- the server
def port_listening(port, host="127.0.0.1"):
    try:
        with socket.create_connection((host, int(port)), timeout=2):
            return True
    except OSError:
        return False


def server_status(app, ps_run=None):
    """{'running', 'port', 'task': 'boot' | 'logon' | '', 'task_state', 'account'}"""
    s = settings(app)
    st = {"running": port_listening(s["port"]), "port": s["port"], "task": "", "task_state": "", "account": ""}
    if ps_run:
        code, out = ps_run("foreach ($n in %s) { $t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue; if ($t) { Write-Output ('TASK=' + $n + '|' + $t.State + '|' + $t.Principal.UserId) } }"
                           % ("'%s','%s'" % (BOOT_TASK, LOGON_TASK)))
        for m in re.finditer(r"TASK=([^|]+)\|([^|]*)\|(.*)", out or ""):
            if not st["task"] or m.group(1) == BOOT_TASK:
                st["task"] = "boot" if m.group(1) == BOOT_TASK else "logon"
                st["task_state"], st["account"] = m.group(2).strip(), m.group(3).strip()
    return st


def status_text(st):
    how = {"boot": "يبدأ عند إقلاع الجهاز دون دخول أحد (بحساب %s)" % (st["account"] or "النظام"), "logon": "يبدأ بعد دخول المستخدم %s إلى ويندوز" % st["account"],
           "": "لا يوجد تشغيل تلقائي: يُشغَّل يدوياً"}[st["task"]]
    return "الخادم %s على المنفذ %d · %s" % ("يعمل ✔" if st["running"] else "متوقف ✘", st["port"], how)


def stop_server(app, ps_run):
    """Clean stop: the tasks are stopped first (they would start it again), then the server is asked to finish and exit."""
    s = settings(app)
    ps_run("foreach ($n in '%s','%s') { Stop-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue }" % (BOOT_TASK, LOGON_TASK))
    script = os.path.join(app, "deploy", "stop_server.ps1")
    if os.path.isfile(script):
        ps_run("& '%s' -Port %d" % (script.replace("'", "''"), s["port"]))
    if port_listening(s["port"]):
        ps_run("Get-NetTCPConnection -LocalPort %d -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }" % s["port"])
    return not port_listening(s["port"])


def start_server(app, ps_run, wait=40, sleeper=None):
    """Starts through the scheduled task when there is one (so it runs under the usual account), else directly. Returns True when the port answers."""
    import time
    s = settings(app)
    if port_listening(s["port"]):
        return True
    st = server_status(app, ps_run)
    if st["task"]:
        ps_run("Start-ScheduledTask -TaskName '%s'" % (BOOT_TASK if st["task"] == "boot" else LOGON_TASK))
    else:
        logs = os.path.join(app, "logs")
        os.makedirs(logs, exist_ok=True)
        out = open(os.path.join(logs, "server.out.txt"), "ab")
        subprocess.Popen([find_python(app), "serve.py"], cwd=app, stdout=out, stderr=out, env=dict(os.environ, PYTHONUTF8="1"),
                         creationflags=(0x00000008 | 0x00000200) if os.name == "nt" else 0)
    for _ in range(wait):
        (sleeper or time.sleep)(1)
        if port_listening(s["port"]):
            return True
    return False

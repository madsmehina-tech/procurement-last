# -*- coding: utf-8 -*-
"""The admin panel logic: config.ini is changed safely, the three backup folders are checked, the system's tools are driven without a console."""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import po_admin as ad  # noqa: E402
import po_setup as ps  # noqa: E402

INI = """; written by setup
[database]
path = {db}

[storage]
archive_root = {arch}   ; the share
legacy_uploads = uploads
backup_dir = {base}/backups
backup_keep = 20
; To enable ... remove the ; at the start of the next line
; backup_archive_dir = F:\\PO_BackupSet

[server]
host = 0.0.0.0
port = 5055
"""


@pytest.fixture
def app(tmp_path):
    base = tmp_path / "PO"
    a = base / "app"
    (a / "deploy").mkdir(parents=True)
    (base / "data").mkdir()
    (tmp_path / "Archive").mkdir()
    (a / "config.ini").write_text(INI.format(db=base / "data" / "db.accdb", arch=tmp_path / "Archive", base=base), encoding="utf-8")
    (a / "deploy" / "python_path.txt").write_text(sys.executable, encoding="utf-8")
    return str(a)


def test_settings_are_read_like_the_system_reads_them(app, tmp_path):
    s = ad.settings(app)
    assert s["port"] == 5055 and s["archive"] == str(tmp_path / "Archive") and s["legacy"] == os.path.join(app, "uploads")
    assert s["backup_archive_dir"] == "" and s["backup_keep"] == "20"                     # the commented example line is not a setting
    with pytest.raises(ad.AdminError):
        ad.settings(str(tmp_path))


def test_ini_values_are_replaced_added_and_removed_keeping_everything_else(app):
    ini = os.path.join(app, "config.ini")
    bak = ad.set_ini_values(ini, "storage", {"backup_dir": r"E:\B\db", "backup_archive_dir": r"E:\B\set", "backup_extra_dir": ""})
    text = open(ini, encoding="utf-8").read()
    assert "backup_dir = E:\\B\\db" in text and "backup_archive_dir = E:\\B\\set" in text and "; backup_archive_dir = F:\\PO_BackupSet" in text
    assert "archive_root" in text and "; the share" in text and "[server]" in text and "port = 5055" in text and text.count("[storage]") == 1
    assert text.index("backup_archive_dir = E:") < text.index("[server]")                 # inside its section
    assert os.path.isfile(bak) and "backup_dir = E:" not in open(bak, encoding="utf-8").read()
    s = ad.settings(app)
    assert s["backup_archive_dir"] == r"E:\B\set" if os.name == "nt" else s["backup_archive_dir"].endswith("set")
    ad.set_ini_values(ini, "storage", {"backup_archive_dir": ""})
    assert "\nbackup_archive_dir = " not in open(ini, encoding="utf-8").read()
    ad.set_ini_values(ini, "newsection", {"k": "v"})
    assert "[newsection]\r\nk = v" in open(ini, encoding="utf-8", newline="").read()


def test_backup_folders_inside_the_data_are_refused_and_the_same_disk_is_a_warning(app, tmp_path):
    base = os.path.dirname(app)
    errs, _w = ad.check_backup_folders(app, {"backup_dir": os.path.join(base, "backups"), "backup_archive_dir": str(tmp_path / "Archive" / "bk")})
    assert any("داخل مجلد الأرشيف" in e for e in errs)
    errs, _w = ad.check_backup_folders(app, {"backup_dir": os.path.join(app, "bk")})
    assert any("مجلد البرنامج" in e for e in errs)
    errs, _w = ad.check_backup_folders(app, {"backup_dir": ""})
    assert errs
    errs, _w = ad.check_backup_folders(app, {"backup_dir": str(tmp_path / "x"), "backup_archive_dir": str(tmp_path / "x")})
    assert any("مختلفاً" in e for e in errs)
    errs, warns = ad.check_backup_folders(app, {"backup_dir": os.path.join(base, "backups"), "backup_archive_dir": str(tmp_path / "Set")})
    assert errs == [] and any("القرص نفسه" in w for w in warns)                           # allowed, with an honest warning


def test_saving_creates_the_folders_and_writes_the_file(app, tmp_path):
    vals = {"backup_dir": str(tmp_path / "B" / "db"), "backup_extra_dir": "", "backup_archive_dir": str(tmp_path / "B" / "set")}
    bak, warns = ad.save_backup_folders(app, vals, "15", "6")
    s = ad.settings(app)
    assert os.path.isdir(vals["backup_dir"]) and os.path.isdir(vals["backup_archive_dir"]) and s["backup_keep"] == "15" and s["backup_auto_hours"] == "6"
    assert s["backup_archive_dir"] == vals["backup_archive_dir"]
    with pytest.raises(ad.AdminError):
        ad.save_backup_folders(app, vals, "abc", "6")
    with pytest.raises(ad.AdminError):
        ad.save_backup_folders(app, dict(vals, backup_dir=os.path.join(app, "inside")))
    assert ad.settings(app)["backup_dir"] == vals["backup_dir"]                            # a refused save changes nothing


def test_backup_now_needs_the_folder_and_runs_the_systems_own_tool(app, tmp_path):
    with pytest.raises(ad.AdminError):
        ad.backup_now(app)
    ad.save_backup_folders(app, {"backup_dir": str(tmp_path / "db"), "backup_archive_dir": str(tmp_path / "set")})
    calls = []
    code, out = ad.backup_now(app, runner=lambda cmd, cwd, env: (calls.append((cmd, cwd, env)) or (0, "[OK]")))
    assert code == 0 and calls[0][0] == [sys.executable, os.path.join("tools", "backup_all.py")] and calls[0][1] == app and calls[0][2]["PYTHONUTF8"] == "1"


def test_add_user_passes_the_password_in_the_environment_only_and_explains_refusals(app):
    calls = []

    def ok(cmd, cwd, env):
        calls.append((cmd, env))
        return 0, "OK=sara"
    assert "sara" in ad.add_user(app, "sara", "سارة", "مشتريات", 3, "Passw0rd9", runner=ok)
    cmd, env = calls[0]
    assert "Passw0rd9" not in " ".join(cmd) and env["PO_NEW_PASSWORD"] == "Passw0rd9" and env["PO_NEW_ROLE"] == "3" and env["PO_MODE"] == "add"
    with pytest.raises(ad.AdminError, match="موجود"):
        ad.add_user(app, "sara", "", "", 3, "Passw0rd9", runner=lambda c, d, e: (2, "ERR=exists"))
    data = ad.list_roles_users(app, runner=lambda c, d, e: (0, 'noise\nJSON={"roles": [[1, "\\u0645\\u062f\\u064a\\u0631"]], "users": [["admin", "active"]]}\n'))
    assert data["roles"] == [[1, "مدير"]] and data["users"] == [["admin", "active"]]
    with pytest.raises(ad.AdminError):
        ad.list_roles_users(app, runner=lambda c, d, e: (1, "Traceback"))


def test_the_user_code_is_valid_python():
    compile(ad.USER_CODE, "<user>", "exec")


def test_status_prefers_the_boot_task_and_reads_its_account(app):
    st = ad.server_status(app, lambda script: (0, "TASK=PO Web Server|Ready|D\r\nTASK=PO Web Server (Boot)|Running|SYSTEM\r\n"))
    assert st["task"] == "boot" and st["account"] == "SYSTEM" and st["port"] == 5055 and st["running"] is False
    assert "إقلاع" in ad.status_text(st) and "متوقف" in ad.status_text(st)
    assert ad.server_status(app, lambda script: (0, ""))["task"] == ""


def test_panel_helpers_describe_the_existing_installation(app, tmp_path):
    a = ps.admin_answers(app)
    assert a.app == app and a.base == os.path.dirname(app) and a.share_folder == str(tmp_path / "Archive") and a.port == 5055 and a.python_path == sys.executable
    assert ps.admin_boot(app, True, "svc", "").startswith("✘")
    assert ps.admin_network(app, "Bad Name", "", "").startswith("✘") and ps.admin_network(app, "", "0.0.0.0/0", "").startswith("✘")


def test_shared_python_is_found_checked_and_the_system_is_pointed_at_it(app, monkeypatch):
    prof, shared = r"C:\Users\D\AppData\Local\Programs\Python\Python312\python.exe", r"C:\Program Files\Python311\python.exe"
    assert ps.in_user_profile(prof) and not ps.in_user_profile(shared) and not ps.in_user_profile(r"C:\Users\Public\py\python.exe")
    calls = []

    def runner(cmd):
        calls.append(cmd)
        if "-c" in cmd and cmd[-1] == ps.PROBE_CODE:
            return (0, "%s\n%s\n64\n" % (cmd[0], "3.12.4" if "Users" in cmd[0] else "3.11.9")) if cmd[0] in (prof, shared) else (1, "")
        if "pip" in cmd:
            return 0, "installed"
        return 0, "LIBS=OK"
    assert ps.probe_python(prof, runner) == (prof, "3.12.4") and ps.probe_python("nope.exe", runner) is None
    assert ps.shared_pythons(runner, [prof, shared]) == [(shared, "3.11.9")]
    monkeypatch.setattr(ps, "candidate_pythons", lambda extra=None: [prof, shared])
    monkeypatch.setattr(ps.admin, "port_listening", lambda port, host="127.0.0.1": False)
    assert ps.admin_shared_python(app, prof, runner=runner).startswith("✘")                 # a profile Python is refused
    text = ps.admin_shared_python(app, "", install=False, runner=runner)
    assert text.startswith("✔") and open(os.path.join(app, "deploy", "python_path.txt")).read() == shared
    assert shared in open(os.path.join(app, "po.bat")).read()
    assert any("pip" in c and c[0] == shared and "requirements.txt" in c for c in calls)
    assert "أصلاً" in ps.admin_shared_python(app, "", install=False, runner=runner)        # second time: nothing to do
    monkeypatch.setattr(ps, "candidate_pythons", lambda extra=None: [prof])
    (open(os.path.join(app, "deploy", "python_path.txt"), "w")).write(prof)
    assert ps.admin_shared_python(app, "", install=False, runner=runner).startswith("✘")    # none found and installing not allowed here


def test_a_failed_library_install_leaves_the_old_python_in_place(app, monkeypatch):
    shared = r"C:\Program Files\Python311\python.exe"
    before = open(os.path.join(app, "deploy", "python_path.txt")).read()

    def runner(cmd):
        if cmd[-1] == ps.PROBE_CODE:
            return 0, "%s\n3.11.9\n64\n" % cmd[0]
        return (1, "no internet") if "pip" in cmd else (0, "LIBS=OK")
    monkeypatch.setattr(ps, "candidate_pythons", lambda extra=None: [shared])
    assert ps.admin_shared_python(app, "", install=False, runner=runner).startswith("✘")
    assert open(os.path.join(app, "deploy", "python_path.txt")).read() == before


def test_a_python_under_program_files_is_found_although_its_path_has_a_space(tmp_path, monkeypatch):
    folder = tmp_path / "Program Files" / "Python312"
    folder.mkdir(parents=True)
    exe = folder / "python.exe"
    exe.write_bytes(b"")

    class P:
        returncode = 0
        stdout = ("%s\r\n3.12.8\r\n64\r\n" % exe).encode()
    monkeypatch.setattr(ps, "candidate_pythons", lambda extra=None: [str(exe)])
    monkeypatch.setattr(ps.subprocess, "run", lambda *a, **k: P())
    assert ps.find_python(ps.Ctx(ps.Answers())) == str(exe)

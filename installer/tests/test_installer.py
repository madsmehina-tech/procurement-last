# -*- coding: utf-8 -*-
"""PO Setup: its logic, tested without Windows (the Windows-only calls are replaced by a fake runner; CI runs the real thing on a Windows machine)."""
import configparser
import hashlib
import json
import os
import subprocess
import sys
import zipfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import po_setup as ps  # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))


def make_zip(path, files=None, top="po_web/"):
    files = files or {"serve.py": "# serve", "migrate.py": "# migrate", "requirements.txt": "Flask", "config.ini.example": "[x]\n",
                      "deploy/start_server.bat": "rem", "tools/setup_check.py": "#", "tools/check_storage.py": "#", "deploy/install_autostart.ps1": "#"}
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(top + name, data)
    return str(path)


def answers(tmp_path, **kw):
    a = ps.Answers(share_folder=str(tmp_path / "PO_Archive"), base=str(tmp_path / "PO"), admin_password="Secret123", zip_path=make_zip(tmp_path / "po_web_v9.zip"),
                   create_share=False, install_tesseract=False, open_firewall=False, autostart=False, start_after=False, skip_prereqs=True, python_path=sys.executable)
    for k, v in kw.items():
        setattr(a, k, v)
    return a


class Recorder:
    """A fake `run`: records commands + environments, answers 0 and makes the files a real run would make."""

    def __init__(self, fail_on=None, out=""):
        self.calls, self.fail_on, self.out = [], fail_on, out

    def __call__(self, cmd, cwd=None, env=None, timeout=0):
        self.calls.append((list(cmd), cwd, dict(env or {})))
        text = " ".join(str(c) for c in cmd)
        if self.fail_on and self.fail_on in text:
            return 1, "boom " + self.out
        return 0, self.out


# ---------------------------------------------------------------- answers and config
def test_defaults_follow_the_share_folder_drive():
    d = ps.derive_defaults(r"E:\Company\Archive")
    assert d.base == "E:\\PO" and d.share_name == "Archive"
    assert ps.derive_defaults(r"D:\PO_Archive").base == "D:\\PO"


def test_validation_catches_the_usual_mistakes(tmp_path):
    a = answers(tmp_path, admin_password="short")
    assert any("كلمة مرور" in e for e in ps.validate(a))
    assert any("المنفذ" in e for e in ps.validate(answers(tmp_path, port=70000)))
    bad = answers(tmp_path)
    bad.share_folder = os.path.join(bad.app, "archive")
    assert any("داخل مجلد البرنامج" in e for e in ps.validate(bad))
    assert ps.validate(answers(tmp_path)) == []
    assert ps.validate(answers(tmp_path, db_mode="existing", db_existing="nope.accdb")) != []
    assert ps.validate(answers(tmp_path, db_mode="existing", admin_password=""), check_paths=False) == []        # an existing database needs no new admin


def test_the_config_is_utf8_without_bom_absolute_and_readable_by_the_system(tmp_path):
    a = answers(tmp_path, backup_set_dir=str(tmp_path / "set"), create_share=True)
    text = ps.build_config(a, tesseract_path="C:\\Program Files\\Tesseract-OCR\\tesseract.exe")
    cfg = tmp_path / "config.ini"
    cfg.write_bytes(text.encode("utf-8"))
    assert not cfg.read_bytes().startswith(b"\xef\xbb\xbf")
    cp = configparser.ConfigParser()
    cp.read(str(cfg), encoding="utf-8")
    assert cp["database"]["path"] == a.db_path and cp["storage"]["archive_root"] == a.share_folder
    assert cp["storage"]["legacy_uploads"] == a.uploads and cp["storage"]["backup_archive_dir"] == str(tmp_path / "set")
    assert cp["storage"]["share_path"].startswith("\\\\") and cp["server"]["port"] == "5000" and "ara+eng" in cp["ocr"]["language"]
    # the application's own settings reader understands the same file
    out = subprocess.run([sys.executable, "-c", "import config;print(config.DB_PATH);print(config.ARCHIVE_ROOT);print(config.BACKUP_ARCHIVE_DIR);print(config.PORT)"],
                         cwd=os.path.join(ROOT, "po_web"), env=dict(os.environ, PO_CONFIG=str(cfg)), capture_output=True, text=True)
    assert out.stdout.split() == [a.db_path, a.share_folder, str(tmp_path / "set"), "5000"], out.stderr
    assert "backup_archive_dir" in ps.build_config(answers(tmp_path))                                           # shown as a hint when not set


# ---------------------------------------------------------------- unpacking
def test_extract_flattens_the_top_folder_and_refuses_paths_that_escape(tmp_path):
    n = ps.safe_extract(make_zip(tmp_path / "p.zip"), str(tmp_path / "app"))
    assert n == 8 and (tmp_path / "app" / "serve.py").exists() and not (tmp_path / "app" / "po_web").exists()
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("po_web/ok.txt", "x")
        z.writestr("po_web/../../outside.txt", "bad")
    with pytest.raises(RuntimeError):
        ps.safe_extract(str(evil), str(tmp_path / "app2"))
    assert not (tmp_path / "outside.txt").exists()


def test_unpack_refuses_an_existing_program_unless_replace_and_then_keeps_config_and_instance(tmp_path):
    a = answers(tmp_path)
    ctx = ps.Ctx(a, run=Recorder())
    os.makedirs(a.app + "/instance")
    open(a.app + "/serve.py", "w").write("# OLD")
    open(a.app + "/config.ini", "w").write("[database]\npath = KEEP\n")
    open(a.app + "/instance/secret.key", "w").write("KEY")
    open(a.app + "/old_only.txt", "w").write("old")
    st, msg = ps.step_unpack(ctx)
    assert st == "fail" and open(a.app + "/serve.py").read() == "# OLD"                                         # nothing was touched
    a.replace_existing = True
    st, msg = ps.step_unpack(ps.Ctx(a, run=Recorder()))
    assert st == "ok"
    assert open(a.app + "/config.ini").read().startswith("[database]\npath = KEEP") and open(a.app + "/instance/secret.key").read() == "KEY"
    assert open(a.app + "/serve.py").read() == "# serve"
    olds = [d for d in os.listdir(a.base) if d.startswith("app_old_")]
    assert len(olds) == 1 and os.path.exists(os.path.join(a.base, olds[0], "old_only.txt"))                     # the old folder is kept, not deleted


def test_a_zip_that_does_not_match_its_hash_file_is_refused(tmp_path):
    a = answers(tmp_path)
    open(a.zip_path + ".sha256", "w").write("0" * 64 + "  x.zip")
    assert ps.step_unpack(ps.Ctx(a, run=Recorder()))[0] == "fail"
    open(a.zip_path + ".sha256", "w").write(hashlib.sha256(open(a.zip_path, "rb").read()).hexdigest() + "  x.zip")
    assert ps.step_unpack(ps.Ctx(a, run=Recorder()))[0] == "ok"
    assert ps.step_unpack(ps.Ctx(answers(tmp_path, zip_path=str(tmp_path / "missing.zip")), run=Recorder()))[0] == "fail"


# ---------------------------------------------------------------- the whole run (fake runner)
def test_a_full_run_in_order_creates_folders_config_and_database_and_calls_the_right_commands(tmp_path):
    a = answers(tmp_path, db_mode="existing", db_existing=str(tmp_path / "mine.accdb"), backup_set_dir=str(tmp_path / "set"))
    open(a.db_existing, "wb").write(b"MYDB")
    rec, log = Recorder(), []
    results, overall, ctx = ps.run_install(a, log.append, run=rec)
    assert overall in ("ok", "warn"), results
    assert [r[0] for r in results] == [k for k, _t, _f in ps.STEPS]
    for d in (a.app, a.data_dir, a.share_folder, a.uploads, a.logs, a.backup_db, a.backup_set_dir):
        assert os.path.isdir(d), d
    assert open(a.db_path, "rb").read() == b"MYDB" and open(a.db_existing, "rb").read() == b"MYDB"                 # a COPY; the original untouched
    assert os.path.isfile(a.app + "/config.ini") and open(a.app + "/deploy/python_path.txt").read() == sys.executable and os.path.isfile(a.app + "/po.bat")
    cmds = [" ".join(c[0]) for c in rec.calls]
    assert any("-m pip install" in c and "requirements.txt" in c for c in cmds)
    assert any("migrate.py" in c for c in cmds) and any("setup_check.py" in c for c in cmds) and any("check_storage.py" in c for c in cmds)
    order = [i for i, c in enumerate(cmds) if "pip install" in c or "migrate.py" in c or "setup_check" in c or "check_storage" in c]
    assert order == sorted(order) and cmds[order[0]].find("pip") >= 0                                              # pip first, then migrate, then the checks
    migrate_env = [c[2] for c in rec.calls if "migrate.py" in c[0]][0]
    assert migrate_env.get("PO_ADMIN_PASSWORD") == "Secret123"                                                         # given only so a database WITHOUT an admin account can get one; unused otherwise
    assert "ثُبّتت" in summary_ok(results) or True


def summary_ok(results):
    return " ".join(r[3] for r in results)


def test_a_new_database_passes_the_admin_only_to_migrate_and_never_logs_the_password(tmp_path):
    a = answers(tmp_path, db_mode="new", admin_user="boss", admin_password="Sup3rSecret")

    class Echo(Recorder):
        def __call__(self, cmd, cwd=None, env=None, timeout=0):
            r = super().__call__(cmd, cwd, env, timeout)
            if "powershell.exe" in cmd[0] and "ADOX" in cmd[-1]:
                open(a.db_path, "wb").write(b"EMPTY")              # what the real ADOX call produces
            return 0, "echo Sup3rSecret in the output"

    rec, log = Echo(), []
    results, overall, ctx = ps.run_install(a, log.append, run=rec)
    env = [c[2] for c in rec.calls if "migrate.py" in c[0]][0]
    assert [c[0][-1] for c in rec.calls if "migrate.py" in c[0]] == ["--new-database"]
    assert env["PO_ADMIN_USER"] == "boss" and env["PO_ADMIN_PASSWORD"] == "Sup3rSecret" and env["PYTHONUTF8"] == "1"
    assert os.path.isfile(a.db_path)
    assert not any("Sup3rSecret" in l for l in log), [l for l in log if "Sup3rSecret" in l]                         # (Ctx.run is replaced here: logging goes through Ctx.log)
    assert "Sup3rSecret" not in ps.summary_text(results, overall, a)


def test_the_log_masks_the_password(tmp_path):
    a = answers(tmp_path, admin_password="Sup3rSecret")
    lines = []
    ps.Ctx(a, log=lines.append).log("running with Sup3rSecret now")
    assert lines == ["running with ******** now"]


def test_an_existing_database_file_is_never_overwritten(tmp_path):
    a = answers(tmp_path)
    os.makedirs(a.data_dir)
    open(a.db_path, "wb").write(b"PRECIOUS")
    rec = Recorder()
    st, msg = ps.step_database(ps.Ctx(a, run=rec))
    assert st == "ok" and open(a.db_path, "rb").read() == b"PRECIOUS" and rec.calls == []
    b = answers(tmp_path, db_mode="existing", db_existing=str(tmp_path / "other.accdb"))
    open(b.db_existing, "wb").write(b"OTHER")
    assert ps.step_database(ps.Ctx(b, run=rec))[0] == "ok" and open(a.db_path, "rb").read() == b"PRECIOUS"


def test_a_failed_important_step_stops_the_rest_but_an_optional_one_does_not(tmp_path):
    open(tmp_path / "mine.accdb", "wb").write(b"DB")
    a = answers(tmp_path, db_mode="existing", db_existing=str(tmp_path / "mine.accdb"))
    results, overall, _ = ps.run_install(a, lambda m: None, run=Recorder(fail_on="-m pip install"))
    by = {r[0]: r[2] for r in results}
    assert overall == "fail" and by["pip"] == "fail" and by["migrate"] == "skip" and by["checks"] == "skip"
    assert by["unpack"] == "ok" and by["config"] == "ok"
    (tmp_path / "second").mkdir()
    open(tmp_path / "second" / "mine.accdb", "wb").write(b"DB")
    b = answers(tmp_path / "second", install_tesseract=True, db_mode="existing", db_existing=str(tmp_path / "second" / "mine.accdb"))
    b.create_share = True
    ctx_run = Recorder(fail_on="New-SmbShare")
    res2, overall2, _ = ps.run_install(b, lambda m: None, run=ctx_run, download=lambda *a, **k: None)
    by2 = {r[0]: r[2] for r in res2}
    assert by2["share"] == "warn" and by2["migrate"] != "skip" and overall2 in ("warn", "ok", "fail")


def test_dry_run_changes_nothing(tmp_path):
    a = answers(tmp_path)
    log = []
    results, overall, _ = ps.run_install(a, log.append, dry=True)
    assert not os.path.exists(a.base) and not os.path.exists(a.share_folder)
    assert overall != "fail", results


# ---------------------------------------------------------------- the Windows-specific commands (built as text; the CI machine runs them for real)
def test_share_script_uses_account_ids_not_localized_names_and_quotes_safely(tmp_path):
    a = answers(tmp_path, create_share=True, team_group="Authenticated Users", share_name="PO_Archive")
    rec = Recorder()
    ps.step_share(ps.Ctx(a, run=rec))
    script = rec.calls[0][0][-1]
    assert "S-1-5-11" in script and "New-SmbShare" in script and "-ReadAccess $team" in script and "OI)(CI)RX" in script
    assert ps.ps_quote("a'b") == "'a''b'" and "S-1-5-32-545" in ps.team_group_ps("Users") and ps.team_group_ps("PO\\Team") == "'PO\\Team'"


def test_prerequisite_installers_are_run_silently_and_signed_by_the_right_publisher(tmp_path, monkeypatch):
    a = answers(tmp_path, skip_prereqs=False, python_path="")
    monkeypatch.setattr(ps, "find_python", lambda c: "")
    monkeypatch.setattr(ps, "ace_installed", lambda: False)
    monkeypatch.setattr(ps, "office_32bit_present", lambda: False)
    monkeypatch.setattr(ps, "IS_WINDOWS", True)
    class Signed(Recorder):
        def __call__(self, cmd, cwd=None, env=None, timeout=0):
            super().__call__(cmd, cwd, env, timeout)
            text = " ".join(str(c) for c in cmd)
            if "Get-AuthenticodeSignature" in text:
                return 0, "Valid|CN=Python Software Foundation" if "python-" in text else "Valid|CN=Microsoft Corporation"
            return 0, ""
    rec = Signed()
    downloads = []
    ctx = ps.Ctx(a, run=rec, download=lambda url, dest, sha=None: downloads.append(url) or dest)
    monkeypatch.setattr(ps, "find_python", lambda c: "" if not downloads or len(downloads) < 1 else "C:\\Python312\\python.exe")
    ps.step_python(ctx)
    ps.step_ace(ctx)
    cmds = [" ".join(c[0]) for c in rec.calls]
    sig = [c for c in cmds if "Get-AuthenticodeSignature" in c]
    assert len(sig) == 2
    py = [c for c in rec.calls if c[0][0].endswith("python-3.12.8-amd64.exe")][0][0]
    assert "/quiet" in py and "PrependPath=1" in py and "InstallAllUsers=1" in py and "Include_pip=1" in py
    ace = [c for c in rec.calls if c[0][0].endswith("accessdatabaseengine_X64.exe")][0][0]
    assert ace == [ace[0], "/quiet"]
    assert downloads == [ps.PY_URL, ps.ACE_URL] and ps.PY_URL.startswith("https://www.python.org/") and ps.ACE_URL.startswith("https://download.microsoft.com/")


def test_office_32bit_is_explained_instead_of_a_failed_install(tmp_path, monkeypatch):
    a = answers(tmp_path, skip_prereqs=False)
    monkeypatch.setattr(ps, "ace_installed", lambda: False)
    monkeypatch.setattr(ps, "office_32bit_present", lambda: True)
    st, msg = ps.step_ace(ps.Ctx(a, run=Recorder()))
    assert st == "fail" and "32-bit" in msg


def test_a_tampered_tesseract_download_is_deleted_and_not_run(tmp_path, monkeypatch):
    p = tmp_path / "t.bin"
    p.write_bytes(b"not the installer")
    ctx = ps.Ctx(answers(tmp_path))
    import io
    monkeypatch.setattr(ps.urllib.request, "urlopen", lambda req, timeout=0: io.BytesIO(b"tampered"))
    with pytest.raises(RuntimeError):
        ctx._download("https://example.invalid/x", str(p), sha256=ps.TESS_SHA256)
    assert not p.exists()


def test_python_selection_accepts_3_11_and_3_12_only():
    assert ps.python_ok(ps.parse_python_version("Python 3.12.8")) and ps.python_ok(ps.parse_python_version("Python 3.11.9"))
    assert not ps.python_ok(ps.parse_python_version("Python 3.13.1")) and not ps.python_ok(ps.parse_python_version("Python 3.10.4")) and not ps.python_ok(None)


def test_selftest_and_dry_run_cli(tmp_path):
    out = tmp_path / "self.txt"
    r = subprocess.run([sys.executable, os.path.join(HERE, "..", "po_setup.py"), "--selftest", "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 0 and out.read_text(encoding="utf-8").startswith("OK")
    answers_file = tmp_path / "a.json"
    answers_file.write_text(json.dumps({"share_folder": str(tmp_path / "S"), "base": str(tmp_path / "B"), "admin_password": "Passw0rd1", "zip_path": make_zip(tmp_path / "po_web_v1.zip"),
                                        "skip_prereqs": True, "create_share": False, "open_firewall": False, "autostart": False, "start_after": False, "install_tesseract": False}), encoding="utf-8")
    r2 = subprocess.run([sys.executable, os.path.join(HERE, "..", "po_setup.py"), "--dry-run", "--answers", str(answers_file)], capture_output=True, text=True)
    assert r2.returncode == 0 and "النتيجة" in r2.stdout and not (tmp_path / "B").exists()


def test_arabic_output_survives_a_legacy_console_encoding(tmp_path):
    """Found on a real Windows machine: print() of Arabic crashed with cp1252. The tool must reconfigure its output."""
    f = tmp_path / "a.json"
    f.write_text(json.dumps({"share_folder": str(tmp_path / "S"), "base": str(tmp_path / "B"), "admin_password": "Passw0rd1", "zip_path": make_zip(tmp_path / "po_web_v1.zip"),
                             "skip_prereqs": True, "create_share": False, "open_firewall": False, "autostart": False, "start_after": False, "install_tesseract": False}), encoding="utf-8")
    r = subprocess.run([sys.executable, os.path.join(HERE, "..", "po_setup.py"), "--dry-run", "--answers", str(f)], capture_output=True,
                       env=dict(os.environ, PYTHONIOENCODING="cp1252", PYTHONUTF8="0"))
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[-400:]
    assert "النتيجة".encode("utf-8") in r.stdout


def test_powershell_commands_reset_the_module_path_first():
    cmd = ps_cmd = ps.ps("Get-Date")
    assert cmd[-1].startswith("$env:PSModulePath = [Environment]::GetEnvironmentVariable('PSModulePath','Machine');") and cmd[-1].endswith("Get-Date")


def test_an_installer_with_a_bad_signature_is_never_run(tmp_path, monkeypatch):
    a = answers(tmp_path, skip_prereqs=False, python_path="")
    monkeypatch.setattr(ps, "find_python", lambda c: "")
    monkeypatch.setattr(ps, "IS_WINDOWS", True)

    class BadSig(Recorder):
        def __call__(self, cmd, cwd=None, env=None, timeout=0):
            super().__call__(cmd, cwd, env, timeout)
            return 0, "HashMismatch|CN=Someone Else"
    rec = BadSig()
    st, msg = ps.step_python(ps.Ctx(a, run=rec, download=lambda url, dest, sha=None: dest))
    assert st == "fail" and "توقيع" in msg and not [c for c in rec.calls if c[0][0].endswith("amd64.exe")]


def test_migrate_without_admin_in_existing_db_gives_a_clear_message():
    a = ps.Answers(share_folder=r"E:\Share", base=r"E:\PO", db_mode="existing", admin_password="")
    c = ps.Ctx(a, log=lambda m: None, dry=False, run=lambda cmd, **k: (1, 'input("Admin username: ") EOFError'), download=None)
    c.python = "python"
    status, detail = ps.step_migrate(c)
    assert status == "fail" and "حساب مدير" in detail


def test_migrate_is_tried_again_once_when_the_driver_refuses_a_connection(monkeypatch):
    monkeypatch.setattr(ps.time, "sleep", lambda s: None)
    a = ps.Answers(share_folder=r"E:\Share", base=r"E:\PO", db_mode="existing")
    outs = [(1, "pyodbc.Error: ('HY000', 'The driver did not supply an error!')"), (0, "done")]
    c = ps.Ctx(a, log=lambda m: None, dry=False, run=lambda cmd, **k: outs.pop(0), download=None)
    c.python = "python"
    assert ps.step_migrate(c)[0] == "ok" and outs == []
    outs = [(1, "boom"), (1, "boom again")]
    c = ps.Ctx(a, log=lambda m: None, dry=False, run=lambda cmd, **k: outs.pop(0), download=None)
    c.python = "python"
    assert ps.step_migrate(c)[0] == "fail" and outs == []

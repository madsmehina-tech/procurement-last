# -*- coding: utf-8 -*-
"""v27.9 - what the installer relies on: the first admin can be given through the environment, and the deploy scripts use the installer's Python."""
import builtins
import getpass
import os

import pytest

import conftest
import fake_db

DEPLOY = os.path.join(conftest.ROOT, "deploy")


def test_the_first_admin_comes_from_the_installers_environment_without_any_prompt(monkeypatch):
    fake_db.reset()
    monkeypatch.setenv("PO_ADMIN_USER", "boss")
    monkeypatch.setenv("PO_ADMIN_NAME", "Boss Person")
    monkeypatch.setenv("PO_ADMIN_PASSWORD", "Passw0rd-1")
    monkeypatch.setattr(builtins, "input", lambda *a: pytest.fail("must not ask"))
    monkeypatch.setattr(getpass, "getpass", lambda *a: pytest.fail("must not ask"))
    import importlib
    importlib.import_module("migrations.m001_users").main()
    row = fake_db.query("SELECT [Username], [FullName], [PasswordHash] FROM [Users]")[0]
    assert row["Username"] == "boss" and row["FullName"] == "Boss Person"
    assert row["PasswordHash"] != "Passw0rd-1" and row["PasswordHash"]                      # stored hashed, never as typed
    from werkzeug.security import check_password_hash
    assert check_password_hash(row["PasswordHash"], "Passw0rd-1")


def test_without_the_environment_the_old_interactive_way_still_works(monkeypatch):
    fake_db.reset()
    for k in ("PO_ADMIN_USER", "PO_ADMIN_PASSWORD", "PO_ADMIN_NAME"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(builtins, "input", lambda *a: "typed")
    monkeypatch.setattr(getpass, "getpass", lambda *a: "Typed12345")
    import importlib
    importlib.import_module("migrations.m001_users").main()
    assert fake_db.query("SELECT [Username] FROM [Users]")[0]["Username"] == "typed"


@pytest.mark.parametrize("bat,needle", [("start_server.bat", "serve.py"), ("start_server_loop.bat", "serve.py"), ("start_staging.bat", "serve.py"),
                                         ("migrate_staging.bat", "migrate.py"), ("backup_all.bat", "backup_all.py")])
def test_deploy_scripts_use_the_python_the_installer_chose(bat, needle):
    text = open(os.path.join(DEPLOY, bat), encoding="utf-8").read()
    assert 'set "PO_PY=python"' in text and "python_path.txt" in text                         # falls back to «python» exactly as before
    run_lines = [l for l in text.splitlines() if needle in l and not l.lstrip().lower().startswith("rem")]
    assert run_lines and all('"%PO_PY%"' in l for l in run_lines), run_lines
    assert text.index('set "PO_PY=python"') < text.index(needle)                              # defined before it is used

# -*- coding: utf-8 -*-
"""config.ini written the way the documentation shows it must work; /health must tell the truth about the backups (audit M-05, M-06)."""
import importlib

import conftest
import config


def test_a_comment_after_a_value_is_not_part_of_the_value(tmp_path, monkeypatch):
    ini = tmp_path / "c.ini"
    ini.write_text("[storage]\nbackup_auto_hours = 6   ; كل ست ساعات\nbackup_extra_dir  = D:\\PO_Backup_Second        ; قرص آخر\n", encoding="utf-8")
    monkeypatch.setenv("PO_CONFIG", str(ini))
    monkeypatch.delenv("PO_BACKUP_EXTRA_DIR", raising=False)
    monkeypatch.delenv("PO_BACKUP_AUTO_HOURS", raising=False)
    try:
        c = importlib.reload(config)
        assert c.BACKUP_AUTO_HOURS == 6
        assert "قرص" not in c.BACKUP_EXTRA_DIR and c.BACKUP_EXTRA_DIR.rstrip("\\/").endswith("PO_Backup_Second")
    finally:
        monkeypatch.setenv("PO_CONFIG", conftest.os.environ["PO_CONFIG"])
        importlib.reload(config)


def test_health_shows_a_warning_when_there_is_no_recent_backup():
    conftest.build_schema()
    import app as appmod
    r = appmod.app.test_client().get("/health")
    body = r.get_json()
    assert r.status_code == 200 and body["status"] in ("ok", "warning")
    assert "warnings" in body["backup"]

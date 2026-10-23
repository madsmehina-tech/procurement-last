# -*- coding: utf-8 -*-
"""The backup is the safety net: it must always be complete, always show its own age, and keep a month of history (audit C-08)."""
import os
import time
from datetime import datetime, timedelta

import pytest

import conftest  # noqa: F401
import backup
import config


@pytest.fixture()
def area(tmp_path, monkeypatch):
    db_file = tmp_path / "data" / "PurchaseSystem.accdb"
    db_file.parent.mkdir()
    db_file.write_bytes(b"x" * 5000)
    old = time.time() - 20 * 3600
    os.utime(db_file, (old, old))                                  # the database was last written 20 hours ago
    monkeypatch.setattr(config, "DB_PATH", str(db_file))
    monkeypatch.setattr(config, "BACKUP_DIR", str(tmp_path / "b1"))
    monkeypatch.setattr(config, "BACKUP_EXTRA_DIR", str(tmp_path / "b2"))
    monkeypatch.setattr(config, "BACKUP_AUTO_HOURS", 12)
    monkeypatch.setattr(config, "BACKUP_KEEP", 3)
    backup._STATE["last_error"] = None
    return tmp_path


def test_a_backup_is_as_new_as_the_moment_it_was_made(area):
    dest = backup.backup_database("auto")
    assert (datetime.now() - datetime.fromtimestamp(os.path.getmtime(dest))).total_seconds() < 60
    assert backup.status()["age_hours"] < 0.1                       # NOT 20 hours (the age of the database file)


def test_a_quiet_database_does_not_trigger_a_backup_every_ten_minutes(area):
    assert backup.ensure_recent() is not None
    assert backup.ensure_recent() is None
    assert backup.ensure_recent() is None
    assert len(os.listdir(area / "b1")) == 1


def test_the_second_place_gets_the_same_complete_copy(area):
    dest = backup.backup_database("auto")
    assert (area / "b2" / os.path.basename(dest)).stat().st_size == 5000


def test_a_failed_copy_leaves_no_broken_backup(area, monkeypatch):
    import shutil

    def dies_half_way(src, dst, *a, **k):
        with open(dst, "wb") as f:
            f.write(b"x" * 100)
        raise OSError("disk full")
    monkeypatch.setattr(shutil, "copyfile", dies_half_way)
    with pytest.raises(OSError):
        backup.backup_database("auto")
    assert os.listdir(area / "b1") == []                            # no final file, no leftover .part
    assert backup.last_backup() == (None, None)


def test_a_truncated_copy_is_rejected(area, monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "copyfile", lambda s, d, *a, **k: open(d, "wb").write(b"x" * 10))     # "succeeds" but the file is short
    with pytest.raises(OSError):
        backup.backup_database("auto")
    assert backup.last_backup() == (None, None)


def test_the_second_place_failing_does_not_stop_the_main_backup(area, monkeypatch):
    monkeypatch.setattr(config, "BACKUP_EXTRA_DIR", str(area / "no_such_drive" / "x"))
    real = os.makedirs

    def boom(p, *a, **k):
        if "no_such_drive" in str(p):
            raise OSError("drive not ready")
        return real(p, *a, **k)
    monkeypatch.setattr(os, "makedirs", boom)
    assert backup.backup_database("auto") is not None
    assert "تعذّر النسخ للمكان الثاني" in backup.status()["second_place_error"]


def test_history_reaches_back_a_month_not_a_few_hours(area):
    folder = area / "b1"
    folder.mkdir()
    for d in range(0, 10):                                           # 4 copies a day for 10 days
        for h in (2, 8, 14, 20):
            day = datetime.now() - timedelta(days=d)
            name = "PurchaseSystem_%s_%02d0000_auto.accdb" % (day.strftime("%Y%m%d"), h)
            (folder / name).write_bytes(b"x")
    backup.prune()
    kept = os.listdir(folder)
    days = {n.split("_")[1] for n in kept}
    assert len(days) == 10                                           # every day still has a copy
    assert len(kept) <= 10 + config.BACKUP_KEEP


def test_foreign_files_in_the_folder_are_never_touched(area):
    folder = area / "b1"
    folder.mkdir()
    (folder / "PurchaseSystem_BEFORE_DRILL.accdb").write_bytes(b"keep me")
    for i in range(6):
        (folder / ("PurchaseSystem_2026010%d_120000_manual.accdb" % (i + 1))).write_bytes(b"x")
    backup.prune()
    assert (folder / "PurchaseSystem_BEFORE_DRILL.accdb").exists()


def test_health_warns_when_backups_stop(area):
    st = backup.status()
    assert st["warnings"]                                            # nothing has been backed up yet
    backup.backup_database("auto")
    assert not [w for w in backup.status()["warnings"] if "حديثة" in w]

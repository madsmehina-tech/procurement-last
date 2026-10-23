# -*- coding: utf-8 -*-
"""The test-data cleaner: dry run changes nothing; a failed backup stops everything; users and masters survive; files are moved, not deleted."""
import os
from datetime import datetime

import pytest

import archive
import backup
import conftest
import fake_db
import helpers as h
from tools import wipe_test_data as w


@pytest.fixture(autouse=True)
def _schema():
    conftest.build_schema()


def _seed():
    s = h.add_sub("مقاول يبقى")
    c = h.add_cert(s, 1000)
    fake_db.execute_insert("INSERT INTO [Payments] ([CertificateID], [Amount], [PaidAt]) VALUES (?, ?, ?)", (c, 400, datetime.now()))
    fake_db.execute("INSERT INTO [PurchaseOrders] ([PONumber], [CreatedAt]) VALUES ('PO-1', ?)", (datetime.now(),))
    archive.store_document("SUB", str(s), "سجلات", "سجل تجاري", b"%PDF-1", "a.pdf", user="t")
    return s


def _count(t):
    return fake_db.scalar("SELECT COUNT(*) FROM [%s]" % t)


def test_the_plan_only_counts_and_changes_nothing():
    _seed()
    before = dict(w.plan())
    assert before["Certificates"] == 1 and before["PurchaseOrders"] == 1 and before["Payments"] == 1
    assert dict(w.plan()) == before


def test_a_failed_backup_deletes_nothing(monkeypatch):
    _seed()
    monkeypatch.setattr(backup, "backup_database", lambda *a, **k: None)
    with pytest.raises(RuntimeError):
        w.apply()
    assert _count("Certificates") == 1 and _count("PurchaseOrders") == 1


def test_apply_clears_the_data_keeps_users_and_contractors_and_moves_files(monkeypatch):
    s = _seed()
    users = _count("Users")
    rel = fake_db.query("SELECT [RelPath] FROM [Documents]")[0]["RelPath"]
    monkeypatch.setattr(backup, "backup_database", lambda *a, **k: "/tmp/copy.accdb")
    r = w.apply()
    for t in ("Certificates", "Payments", "PurchaseOrders", "Documents", "NumberSeries"):
        assert _count(t) == 0, t
    assert _count("Users") == users and _count("Subcontractors") == 1       # kept
    assert not os.path.exists(archive.abs_path(rel))                        # no longer where it was ...
    assert r["files_moved_to"] and os.path.exists(os.path.join(r["files_moved_to"], rel.replace("/", os.sep)))   # ... but not deleted
    assert fake_db.query("SELECT * FROM [AuditLog] WHERE [ActionName] = 'wipe_test_data'")

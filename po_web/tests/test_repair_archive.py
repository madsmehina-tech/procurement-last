# -*- coding: utf-8 -*-
"""The archive repair tool: the preview changes NOTHING, and two different files with the same name are never guessed between (audit M-20)."""
import os
import tempfile
from datetime import datetime

import pytest

import archive
import config
import conftest
import fake_db
from tools import repair_archive as ra


@pytest.fixture()
def old_contract(monkeypatch):
    conftest.build_schema()
    sub = fake_db.execute_insert("INSERT INTO [Subcontractors] ([Name], [CreatedAt]) VALUES ('مقاول', ?)", (datetime.now(),))
    cid = fake_db.execute_insert("INSERT INTO [Contracts] ([SubcontractorID], [Project], [ContractFile], [CreatedAt]) VALUES (?, 'م', 'contracts/1/عقد.pdf', ?)", (sub, datetime.now()))
    legacy = tempfile.mkdtemp()
    monkeypatch.setattr(config, "LEGACY_UPLOADS", legacy)
    return cid, legacy


def _snapshot():
    root = archive.abs_path("")
    tree = sorted(os.path.join(d, n) for d, ds, fs in os.walk(root) for n in ds + fs) if os.path.isdir(root) else []
    return tree, fake_db.query("SELECT * FROM [Contracts]"), fake_db.query("SELECT * FROM [Documents]")


def test_the_preview_creates_no_folder_and_changes_no_record(old_contract, capsys):
    cid, legacy = old_contract
    os.makedirs(os.path.join(legacy, "misc"))                         # not where the record says, but findable by name
    open(os.path.join(legacy, "misc", "عقد.pdf"), "wb").write(b"%PDF-1")
    before = _snapshot()
    ra.main(["repair_archive.py"])                                   # no --apply
    assert _snapshot() == before
    assert "سيُنسخ إلى أرشيف السجل" in capsys.readouterr().out


def test_two_different_files_with_the_same_name_are_not_guessed(old_contract, capsys):
    cid, legacy = old_contract
    for folder, size in (("a", 10), ("b", 20)):
        os.makedirs(os.path.join(legacy, folder))
        open(os.path.join(legacy, folder, "عقد.pdf"), "wb").write(b"x" * size)
    ra.main(["repair_archive.py", "--apply"])
    out = capsys.readouterr().out
    assert "لن أختار عنك" in out
    assert fake_db.query("SELECT [ContractFile] FROM [Contracts]")[0]["ContractFile"] == "contracts/1/عقد.pdf"        # untouched

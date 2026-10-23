# -*- coding: utf-8 -*-
"""Saving an archive document: a failed save loses nothing and leaves no orphan file; long names are shortened (audit M-18, M-19)."""
import os

import pytest

import archive
import conftest
import fake_db


@pytest.fixture(autouse=True)
def _schema():
    conftest.build_schema()


def _docs(key="1"):
    return fake_db.query("SELECT * FROM [Documents] WHERE [EntityType] = 'SUB' AND [EntityKey] = ?", (key,))


def _store(folder, name="x.pdf", doc_type="سجل تجاري"):
    return archive.store_document("SUB", "1", folder, doc_type, b"%PDF-1", name, user="t")


def test_a_long_folder_gets_a_shorter_file_name_that_still_fits():
    folder = "مقاولو/" + "ا" * 80 + "/" + "ب" * 80
    row = _store(folder, "عقد " + "ج" * 80 + ".pdf")
    assert len(row["RelPath"]) <= archive.MAX_REL and row["RelPath"].endswith(".pdf")
    assert os.path.exists(archive.abs_path(row["RelPath"]))


def test_a_folder_too_deep_for_any_file_is_refused_cleanly_with_no_file_left():
    folder = "ا" * 120 + "/" + "ب" * 120
    with pytest.raises(archive.PathTooLong):
        _store(folder)
    assert _docs() == []


def test_a_failed_database_save_keeps_the_old_document_and_leaves_no_orphan(monkeypatch):
    first = _store("سجلات", "a.pdf")
    before = set(os.listdir(archive.abs_path("سجلات")))
    import db

    class Boom:
        def __enter__(self):
            raise RuntimeError("database refused")

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(db, "transaction", lambda: Boom())
    with pytest.raises(RuntimeError):
        _store("سجلات", "b.pdf")
    monkeypatch.undo()
    assert set(os.listdir(archive.abs_path("سجلات"))) == before          # the new file is gone
    cur = [d for d in _docs() if d["IsCurrent"]]
    assert len(cur) == 1 and cur[0]["RelPath"] == first["RelPath"]       # the earlier document is still the current one

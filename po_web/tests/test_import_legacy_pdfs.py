# -*- coding: utf-8 -*-
"""Importing the PDFs of the old tracker into the archive: preview changes nothing, apply copies once, never duplicates, never touches the old files."""
import os
import sys
from datetime import datetime

import pytest

import archive
import conftest
import fake_db

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import import_legacy_pdfs as imp  # noqa: E402

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


@pytest.fixture(autouse=True)
def _schema():
    conftest.build_schema()


def add_po(po, supplier="مورد", project="مشروع"):
    fake_db.execute("INSERT INTO [PurchaseOrders] ([PONumber], [SupplierName], [Project], [TotalAmount], [CreatedAt]) VALUES (?, ?, ?, 100, ?)", (po, supplier, project, datetime.now()))


def row(i, typ, po, path, saved=None):
    return {"id": str(i), "type": typ, "po": po, "original": "x.pdf", "saved": saved or os.path.basename(path), "path": path, "added": datetime(2026, 9, 20, 11, 0)}


def files(tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    (old / "PO 001.pdf").write_bytes(PDF)
    (old / "Qu 1.pdf").write_bytes(PDF + b"\n%q")
    return old


def decide(rows, old, maps=None):
    return imp.plan(rows, maps or [], imp.index_files([str(old)]), imp.load_orders(), imp.hashes_of_order)


def test_numbers_match_ignoring_case_and_dashes():
    assert imp.norm_po("po-2026-383") == imp.norm_po("PO 2026383") == "po2026383"


def test_the_old_network_path_is_mapped_to_the_local_one():
    maps = imp.parse_maps([r"\\192.168.100.55\Purchases Folder Shared=D:\Purchases Folder Shared"])
    assert imp.apply_maps(r"\\192.168.100.55\Purchases Folder Shared\0-PO Tracker\a.pdf", maps) == os.path.join(r"D:\Purchases Folder Shared", r"0-PO Tracker\a.pdf")
    assert imp.apply_maps(r"X:\other\a.pdf", maps) == r"X:\other\a.pdf"


def test_preview_classifies_and_writes_nothing(tmp_path):
    old = files(tmp_path)
    add_po("PO-2026-383")
    rows = [row(1, "PO", "po-2026383", str(old / "PO 001.pdf")),             # found, order exists (different dashes)
            row(2, "QU", "PO-9999", str(old / "Qu 1.pdf")),                  # order not in the system
            row(3, "INV", "PO-2026-383", str(old / "nope.pdf"))]            # file not on disk
    before = set(os.listdir(archive.abs_path("")) if os.path.isdir(archive.abs_path("")) else [])
    d = decide(rows, old)
    assert [x["status"] for x in d] == ["will_copy", "order_missing", "file_missing"]
    assert fake_db.query("SELECT * FROM [Documents]") == []
    assert set(os.listdir(archive.abs_path("")) if os.path.isdir(archive.abs_path("")) else []) == before
    assert "ستُنسخ:                      1" in imp.summary(d)


def test_a_file_missing_at_its_path_is_found_by_name_under_the_search_folder(tmp_path):
    old = files(tmp_path)
    add_po("PO 001")
    d = decide([row(1, "PO", "001", r"\\gone\share\PO 001.pdf", saved="PO 001.pdf")], old)
    assert d[0]["status"] == "order_missing"                                   # «001» is not «PO 001»: reported, never guessed
    add_po("001")
    d = decide([row(1, "PO", "001", r"\\gone\share\PO 001.pdf", saved="PO 001.pdf")], old)
    assert d[0]["status"] == "will_copy" and d[0]["src"].endswith("PO 001.pdf")


def test_apply_copies_into_the_order_folder_keeps_the_old_file_and_the_date_and_is_safe_to_repeat(tmp_path):
    old = files(tmp_path)
    add_po("PO-2026-383")
    rows = [row(1, "PO", "po-2026383", str(old / "PO 001.pdf")), row(2, "QU", "PO-2026-383", str(old / "Qu 1.pdf"))]
    d = imp.apply(decide(rows, old))
    assert [x["status"] for x in d] == ["copied", "copied"]
    docs = fake_db.query("SELECT * FROM [Documents] WHERE [EntityType] = 'PO' AND [EntityKey] = 'PO-2026-383'")
    assert sorted(x["DocType"] for x in docs) == ["أمر الشراء", "عرض السعر"]
    for x in docs:
        assert os.path.isfile(archive.abs_path(x["RelPath"])) and "مستورد" in x["Note"]
        assert x["UploadedAt"] == datetime(2026, 9, 20, 11, 0)
    assert (old / "PO 001.pdf").read_bytes() == PDF                              # the old file is untouched
    again = decide(rows, old)                                                    # a second run finds everything already there
    assert [x["status"] for x in again] == ["already_there", "already_there"]
    assert len(fake_db.query("SELECT * FROM [Documents]")) == 2


def test_a_file_that_is_not_a_real_pdf_is_refused_not_stored(tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    (old / "bad.pdf").write_bytes(b"this is not a pdf")
    add_po("PO-1")
    d = imp.apply(decide([row(1, "PO", "PO-1", str(old / "bad.pdf"))], old))
    assert d[0]["status"] == "rejected"
    assert fake_db.query("SELECT * FROM [Documents]") == []


def test_the_same_file_twice_in_the_sheet_is_copied_once(tmp_path):
    old = files(tmp_path)
    add_po("PO-1")
    d = decide([row(1, "PO", "PO-1", str(old / "PO 001.pdf")), row(2, "PO", "PO-1", str(old / "PO 001.pdf"))], old)
    assert [x["status"] for x in d] == ["will_copy", "already_there"]


def test_two_orders_that_differ_only_by_a_dash_are_told_apart_by_the_exact_key(tmp_path):
    old = files(tmp_path)
    add_po("po-2026383")
    add_po("po-2026-383")
    d = decide([row(1, "PO", "po-2026383", str(old / "PO 001.pdf")), row(2, "QU", "po-2026-383", str(old / "Qu 1.pdf")), row(3, "QU", "PO-2026383", str(old / "Qu 1.pdf"))], old)
    assert [(x["status"], x["po_real"]) for x in d] == [("will_copy", "po-2026383"), ("will_copy", "po-2026-383"), ("will_copy", "po-2026383")]   # exact, ignoring case
    add_po("PO-2026-9")
    add_po("po 2026 9")
    d = decide([row(4, "PO", "PO2026-9", str(old / "PO 001.pdf"))], old)                  # no exact key and two loose matches: still reported, never guessed
    assert d[0]["status"] == "order_ambiguous"

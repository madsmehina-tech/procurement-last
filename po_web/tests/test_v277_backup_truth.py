# -*- coding: utf-8 -*-
"""v27.7 - the third review's findings on the backup tools: a backup / restore is «OK» only when it really is."""
import os
import shutil
from datetime import datetime

import pytest

import storage_tools as st
from test_phase3_v276 import make_archive, make_db


def _rel(arch, name):
    return os.path.join(arch, *name.split("/"))


# ---------------------------------------------------------------- a file moved while the backup runs
def test_a_document_moved_after_the_database_copy_is_still_in_the_backup(tmp_path, monkeypatch):
    arch = str(tmp_path / "a")
    os.makedirs(arch + "/orders")
    open(arch + "/orders/old.pdf", "wb").write(b"%PDF-1.4 content")
    db = make_db(str(tmp_path / "p.accdb"))
    real = st._stable_copy_of_db

    def copy_then_move(*a, **k):
        r = real(*a, **k)                                                      # the database copy says: orders/old.pdf
        shutil.move(arch + "/orders/old.pdf", arch + "/orders/new.pdf")        # ... then the application moves the file
        return r
    monkeypatch.setattr(st, "_stable_copy_of_db", copy_then_move)
    reader = lambda f: ["orders/old.pdf"]
    dest = str(tmp_path / "bk")
    b = st.backup_set(db, arch, dest, link_reader=reader)
    assert b["ok"] and b["links"]["checked"] and b["links"]["missing"] == 0
    r = st.restore_set(dest, str(tmp_path / "rs" / "r.accdb"), str(tmp_path / "rs" / "arch"), link_reader=reader)
    assert r["ok"] and r["links_checked"] and r["links_missing_count"] == 0
    assert os.path.isfile(str(tmp_path / "rs" / "arch" / "orders" / "old.pdf"))     # the document the database points at EXISTS after the restore


def test_a_database_pointing_at_a_file_that_is_nowhere_makes_the_backup_not_ok(tmp_path):
    arch = make_archive(str(tmp_path / "a"), 3)
    db = make_db(str(tmp_path / "p.accdb"))
    b = st.backup_set(db, arch, str(tmp_path / "bk"), link_reader=lambda f: ["الموردين/ليس موجوداً.pdf"], rounds=2)
    assert not b["ok"] and b["links"]["missing"] == 1 and b["links"]["examples"] == ["الموردين/ليس موجوداً.pdf"]
    with pytest.raises(OSError):                                                    # an incomplete set is not used for a restore unless somebody insists
        st.restore_set(str(tmp_path / "bk"), str(tmp_path / "r.accdb"), str(tmp_path / "ra"))
    r = st.restore_set(str(tmp_path / "bk"), str(tmp_path / "r.accdb"), str(tmp_path / "ra"), allow_incomplete=True, link_reader=lambda f: ["الموردين/ليس موجوداً.pdf"])
    assert not r["ok"] and r["links_missing_count"] == 1


def test_links_that_could_not_be_read_are_reported_as_not_checked_never_as_checked(tmp_path):
    arch = make_archive(str(tmp_path / "a"), 2)
    db = make_db(str(tmp_path / "p.accdb"))

    def broken(f):
        raise RuntimeError("no Access driver here")
    b = st.backup_set(db, arch, str(tmp_path / "bk"), link_reader=broken)
    assert b["links"]["checked"] is False and "no Access driver" in b["links"]["error"]
    r = st.restore_set(str(tmp_path / "bk"), str(tmp_path / "r.accdb"), str(tmp_path / "ra"), link_reader=broken)
    assert r["links_checked"] is False and not r["ok"]                              # an unproven restore is not an OK restore


# ---------------------------------------------------------------- failures are never hidden
def test_an_unreadable_item_fails_the_backup_and_is_named(tmp_path, monkeypatch):
    arch = make_archive(str(tmp_path / "a"), 4)
    db = make_db(str(tmp_path / "p.accdb"))
    real = st.walk_files

    def with_unreadable(root):
        yield from real(root)
        yield None, 0, 0                                                            # a folder the scanner could not open
    monkeypatch.setattr(st, "walk_files", with_unreadable)
    b = st.backup_set(db, arch, str(tmp_path / "bk"))
    assert not b["ok"] and b["failed"] >= 1 and b["errors"] and "تعذّرت قراءته" in b["errors"][0]
    import backup
    assert "غير مكتملة" in backup.archive_problem(b)
    assert st.read_manifest(b["manifest"])["complete"] is False


def test_a_file_that_cannot_be_copied_fails_the_backup(tmp_path, monkeypatch):
    arch = make_archive(str(tmp_path / "a"), 4)
    db = make_db(str(tmp_path / "p.accdb"))
    real = st._copy_keep_time

    def fail_one(src, dest, mtime):
        if "ملف 1.pdf" in src:
            raise OSError("disk full")
        return real(src, dest, mtime)
    monkeypatch.setattr(st, "_copy_keep_time", fail_one)
    b = st.backup_set(db, arch, str(tmp_path / "bk"))
    assert not b["ok"] and any("disk full" in e for e in b["errors"])


def test_the_command_line_backup_exits_with_failure_when_the_set_is_not_ok(tmp_path, monkeypatch, capsys):
    import runpy
    import sys
    arch = make_archive(str(tmp_path / "a"), 2)
    db = make_db(str(tmp_path / "p.accdb"))
    monkeypatch.setattr(st, "read_document_paths", lambda f: ["غائب.pdf"])
    monkeypatch.setattr(sys, "argv", ["backup_all", "--dest", str(tmp_path / "bk"), "--db", db, "--archive", arch])
    with pytest.raises(SystemExit) as e:
        runpy.run_path(os.path.join(os.path.dirname(__file__), "..", "tools", "backup_all.py"), run_name="__main__")
    assert e.value.code == 1
    assert "غير مكتملة" in capsys.readouterr().out


# ---------------------------------------------------------------- the guard (the review's three cases) + a deep fingerprint
def test_the_guard_reads_a_bom_ini_and_relative_paths_and_effective_settings(tmp_path, monkeypatch):
    import config
    ini = tmp_path / "config.ini"
    arch_abs = str(tmp_path / "PO_Archive")
    ini.write_bytes(("\ufeff[database]\npath = data/PO.accdb\n[storage]\narchive_root = %s\nbackup_dir = bk\n" % arch_abs).encode("utf-8"))        # saved by Notepad with a BOM
    monkeypatch.setattr(config, "CONFIG_FILE", str(ini))
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path / "app"))
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "from_env" / "REAL.accdb"))          # the running configuration (an environment override)
    monkeypatch.delenv("PO_PROTECT", raising=False)
    got = [st.norm(p) for p in st.production_paths()]
    assert st.norm(arch_abs) in got                                                              # the BOM did not hide the archive path
    assert st.norm(str(tmp_path / "from_env" / "REAL.accdb")) in got                            # the path really in use is protected
    assert st.norm(str(tmp_path / "app" / "data" / "PO.accdb")) in got and st.norm(str(tmp_path / "data" / "PO.accdb")) in got      # a relative path, both ways of measuring it
    for bad in (str(tmp_path / "from_env" / "REAL.accdb"), arch_abs, os.path.join(arch_abs, "sub")):
        with pytest.raises(st.ProductionGuard):
            st.guard_sources_are_copies([bad])


def test_an_unreadable_settings_file_stops_everything(tmp_path, monkeypatch):
    import config
    ini = tmp_path / "config.ini"
    ini.write_bytes(b"\xff\xfe\x00garbage [[[ not an ini \x00\x00")
    monkeypatch.setattr(config, "CONFIG_FILE", str(ini))
    monkeypatch.setattr(st, "_read_ini", lambda p: (_ for _ in ()).throw(st.ProductionGuard("cannot read")))
    with pytest.raises(st.ProductionGuard):
        st.production_paths()


def test_the_fingerprint_sees_a_change_deep_inside_the_archive_and_in_the_database(tmp_path):
    arch = make_archive(str(tmp_path / "a"), 6)
    db = make_db(str(tmp_path / "p.accdb"))
    before = (st.fingerprint_path(arch), st.fingerprint_path(db))
    deep = os.path.join(arch, "الموردين", "مشروع 1", "2026", "ملف 1.pdf")
    t = os.stat(deep).st_mtime + 5
    os.utime(deep, (t, t))                                                                  # a file two levels down is touched
    assert st.fingerprint_path(arch) != before[0]
    open(db, "r+b").write(b"X")                                                              # one byte of the database changes
    assert st.fingerprint_path(db)["sha256"] != before[1]["sha256"]

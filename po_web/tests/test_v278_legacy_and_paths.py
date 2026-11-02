# -*- coding: utf-8 -*-
"""v27.8 - documents that live ONLY in the old uploads folder (LEGACY_UPLOADS) are in the backup, and a restore needs no original folder;
and the helper that tells a person where the data really is."""
import os
import runpy
import subprocess
import sys
import zipfile

import pytest

import conftest
import storage_tools as st
from test_phase3_v276 import make_archive, make_db


def _world(tmp_path):
    arch = make_archive(str(tmp_path / "a"), 3)
    legacy = str(tmp_path / "uploads")
    os.makedirs(legacy + "/old")
    open(legacy + "/old/legacy_only.pdf", "wb").write(b"%PDF-1.4 old file\n%%EOF")
    open(legacy + "/old/second.pdf", "wb").write(b"%PDF-1.4 second\n%%EOF")
    return arch, legacy, make_db(str(tmp_path / "p.accdb"))


READER = lambda f: ["old/legacy_only.pdf", "old/second.pdf", "الموردين/مشروع 1/2026/ملف 1.pdf"]


# ---------------------------------------------------------------- backup includes it, restore needs no original
def test_a_document_only_in_the_old_uploads_is_in_the_backup_and_the_restore_stands_alone(tmp_path, monkeypatch):
    import config
    arch, legacy, db = _world(tmp_path)
    dest = str(tmp_path / "bk")
    b = st.backup_set(db, arch, dest, link_reader=READER, legacy_root=legacy)
    assert b["ok"] and b["legacy_files"] == 2 and b["links"]["checked"] and b["links"]["missing"] == 0
    # the originals are GONE (lost computer / folders not reachable) and the configuration points nowhere
    os.rename(arch, arch + ".gone")
    os.rename(legacy, legacy + ".gone")
    monkeypatch.setattr(config, "LEGACY_UPLOADS", str(tmp_path / "does_not_exist"))
    r = st.restore_set(dest, str(tmp_path / "rs" / "r.accdb"), str(tmp_path / "rs" / "arch"), link_reader=READER, legacy_out=str(tmp_path / "rs" / "legacy"), verify="full")
    assert r["ok"] and r["links_checked"] and r["links_missing_count"] == 0
    assert r["legacy_expected"] == 2 and r["legacy_restored"] == 2
    assert open(str(tmp_path / "rs" / "legacy" / "old" / "legacy_only.pdf"), "rb").read() == b"%PDF-1.4 old file\n%%EOF"      # opened from the restored copy


def test_a_restore_that_forgets_the_legacy_folder_is_refused(tmp_path):
    arch, legacy, db = _world(tmp_path)
    dest = str(tmp_path / "bk")
    st.backup_set(db, arch, dest, link_reader=READER, legacy_root=legacy)
    with pytest.raises(OSError) as e:
        st.restore_set(dest, str(tmp_path / "r.accdb"), str(tmp_path / "ra"))
    assert "LEGACY_UPLOADS" in str(e.value)


def test_the_link_check_never_uses_the_original_legacy_folder(tmp_path):
    """The reviewer's case: the file is only in the ORIGINAL legacy folder. The restored copy must not 'find' it there."""
    arch = make_archive(str(tmp_path / "a"), 2)
    db = make_db(str(tmp_path / "p.accdb"))
    legacy = str(tmp_path / "uploads")
    os.makedirs(legacy)
    open(legacy + "/x.pdf", "wb").write(b"%PDF-1.4\n%%EOF")
    dest = str(tmp_path / "bk")
    st.backup_set(db, arch, dest, link_reader=lambda f: ["x.pdf"], legacy_root="")            # a backup made WITHOUT the legacy folder
    r = st.restore_set(dest, str(tmp_path / "r.accdb"), str(tmp_path / "ra"), link_reader=lambda f: ["x.pdf"], legacy_out=str(tmp_path / "rl"), allow_incomplete=True)
    assert not r["ok"] and r["links_missing_count"] == 1                                       # not found in the restored places -> no success


def test_the_second_backup_copies_nothing_new_from_legacy_and_an_unreadable_one_fails_the_set(tmp_path, monkeypatch):
    arch, legacy, db = _world(tmp_path)
    dest = str(tmp_path / "bk")
    st.backup_set(db, arch, dest, link_reader=READER, legacy_root=legacy)
    again = st.backup_set(db, arch, dest, link_reader=READER, legacy_root=legacy)
    assert again["copied"] == 0 and again["legacy_files"] == 2
    real = st._copy_keep_time
    monkeypatch.setattr(st, "_copy_keep_time", lambda src, d, m: (_ for _ in ()).throw(OSError("disk full")) if "second.pdf" in src else real(src, d, m))
    os.remove(os.path.join(dest, "legacy", "old", "second.pdf"))
    bad = st.backup_set(db, arch, dest, link_reader=READER, legacy_root=legacy)
    assert not bad["ok"] and any("second.pdf" in e for e in bad["errors"])


def test_classify_counts_archive_legacy_only_and_missing(tmp_path):
    arch, legacy, _db = _world(tmp_path)
    c = st.classify_links(["old/legacy_only.pdf", "old/second.pdf", "الموردين/مشروع 1/2026/ملف 1.pdf", "nowhere.pdf"], arch, legacy)
    assert (c["paths"], c["in_archive"], c["legacy_only"], c["missing"]) == (4, 1, 2, 1)
    assert st.classify_links(["old/legacy_only.pdf"], arch, None)["missing"] == 1               # without the legacy folder it is «missing»


def test_every_linked_column_exists_in_the_real_schema():
    """No typo in LINK_COLUMNS: each column is in the schema the migrations build (the real Access database has the same names)."""
    conftest.build_schema()
    import fake_db
    for table, col in st.LINK_COLUMNS:
        cols = [r[1].lower() for r in fake_db._conn.execute("PRAGMA table_info(%s)" % table)]
        assert col.lower() in cols, (table, col)


def test_the_legacy_check_tool_counts_and_refuses_production(tmp_path, monkeypatch, capsys):
    arch, legacy, db = _world(tmp_path)
    monkeypatch.setattr(st, "read_linked_paths", lambda f: {"Documents.RelPath": ["old/legacy_only.pdf", "الموردين/مشروع 1/2026/ملف 1.pdf"], "PurchaseOrders.SignedFile": ["old/second.pdf"]})
    tool = os.path.join(os.path.dirname(conftest.__file__), "..", "tools", "legacy_check.py")
    monkeypatch.setattr(sys, "argv", ["legacy_check", "--db", db, "--archive", arch, "--legacy", legacy])
    with pytest.raises(SystemExit) as e:
        runpy.run_path(tool, run_name="__main__")
    out = capsys.readouterr().out
    assert e.value.code == 0 and "في المرفقات القديمة فقط 2" in out.replace("  ", " ") or "2 مستند يعتمد على المرفقات القديمة" in out
    monkeypatch.setenv("PO_PROTECT", db)
    with pytest.raises(SystemExit) as e2:
        runpy.run_path(tool, run_name="__main__")
    assert e2.value.code == 2 and "ممنوع" in capsys.readouterr().out


def test_zero_legacy_documents_says_nothing_special_is_needed(tmp_path, monkeypatch, capsys):
    arch, legacy, db = _world(tmp_path)
    monkeypatch.setattr(st, "read_linked_paths", lambda f: {"Documents.RelPath": ["الموردين/مشروع 1/2026/ملف 1.pdf"]})
    monkeypatch.setattr(sys, "argv", ["legacy_check", "--db", db, "--archive", arch, "--legacy", legacy])
    with pytest.raises(SystemExit):
        runpy.run_path(os.path.join(os.path.dirname(conftest.__file__), "..", "tools", "legacy_check.py"), run_name="__main__")
    assert "صفر مستند" in capsys.readouterr().out


# ---------------------------------------------------------------- the acceptance check hides the original copies during the restore
def test_the_acceptance_restore_check_works_with_legacy_and_puts_the_copies_back(tmp_path):
    import acceptance as ac
    conftest.build_schema()
    import fake_db
    import config
    arch, legacy, dbfile = _world(tmp_path)
    counts = lambda path, tables: {t: int(fake_db.scalar("SELECT COUNT(*) FROM [%s]" % t, default=0) or 0) for t in tables}
    r = ac.check_backup_restore(fake_db, config, dbfile, arch, str(tmp_path / "work"), counter=counts, link_reader=READER, legacy_root=legacy)
    assert r.status == ac.PASS, r.detail
    assert r.data["legacy_files_in_backup"] == 2 and r.data["originals_hidden_during_restore"] == 2
    assert os.path.isdir(arch) and os.path.isdir(legacy)                                       # both copies are back under their own names
    assert not os.path.exists(arch + ".__hidden_for_restore_test__")


# ---------------------------------------------------------------- the command line tool and legacy
def _run(args, env=None):
    root = os.path.dirname(conftest.__file__)
    return subprocess.run([sys.executable, os.path.join(root, "..", "tools", "acceptance_check.py")] + args, capture_output=True, text=True,
                          env=dict(os.environ, PO_CSRF="0", **(env or {})), timeout=120)


def test_the_tool_refuses_a_legacy_copy_that_is_the_production_folder(tmp_path):
    arch, legacy, db = _world(tmp_path)
    cdb = make_db(str(tmp_path / "copy.accdb"))
    carch = make_archive(str(tmp_path / "carch"), 2)
    r = _run(["--db-copy", cdb, "--archive-copy", carch, "--legacy-copy", legacy, "--work", str(tmp_path / "w")], {"PO_PROTECT": legacy})
    assert r.returncode == 2 and "ممنوع" in r.stdout


def test_the_child_treats_a_missing_legacy_copy_as_absent_not_as_the_program_folders_uploads(tmp_path):
    cdb = make_db(str(tmp_path / "copy.accdb"))
    carch = make_archive(str(tmp_path / "carch"), 2)
    r = _run(["--db-copy", cdb, "--archive-copy", carch, "--work", str(tmp_path / "w")], {"PO_PROTECT": str(tmp_path / "PROD")})
    assert "ممنوع" not in r.stdout and "بدأ اختبار القبول" in r.stdout
    ini = open(str(tmp_path / "w" / "acceptance.ini"), encoding="utf-8").read()
    assert "legacy_uploads = " in ini and "no_legacy_uploads" in ini                            # never the default «uploads» next to the program


# ---------------------------------------------------------------- where_is_my_data (standalone, any version)
def _program_folder(tmp_path, ini_bytes=None, extra=()):
    prog = tmp_path / "po_web"
    (prog / "instance").mkdir(parents=True)
    (prog / "serve.py").write_text("# serve")
    (prog / "app.py").write_text("# app")
    if ini_bytes is not None:
        (prog / "config.ini").write_bytes(ini_bytes)
    for rel, data in extra:
        p = prog / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return prog


def _where(args, env=None):
    root = os.path.dirname(conftest.__file__)
    e = {k: v for k, v in os.environ.items() if not k.startswith("PO_")}
    e.update(env or {})
    return subprocess.run([sys.executable, os.path.join(root, "..", "tools", "where_is_my_data.py")] + args, capture_output=True, text=True, env=e, timeout=60)


def test_where_reads_a_bom_ini_shows_sources_and_flags_data_inside_the_program_folder(tmp_path):
    prog = _program_folder(tmp_path, ("\ufeff[database]\npath = %s\n[storage]\narchive_root = archive\n" % (tmp_path / "DB" / "P.accdb")).encode("utf-8"),
                           extra=[("archive/x.pdf", b"data"), ("uploads/old.pdf", b"old")])
    r = _where([str(prog)])
    assert r.returncode == 0 and "utf-8-sig (BOM)" in r.stdout
    assert str(tmp_path / "DB" / "P.accdb") in r.stdout and "config.ini" in r.stdout
    assert "سيُترك خلفك" in r.stdout                                                           # archive and uploads are inside the program folder
    assert "بيانات داخل مجلد البرنامج" in r.stdout and "الأرشيف" in r.stdout and "المرفقات القديمة" in r.stdout


def test_where_reports_the_key_source_without_the_key_and_env_overrides(tmp_path):
    prog = _program_folder(tmp_path, b"[database]\npath = D:\\PO\\P.accdb\n", extra=[("instance/secret.key", b"SUPERSECRETKEYVALUE")])
    r = _where([str(prog)], {"PO_SECRET_KEY": "ANOTHERSECRET", "PO_ARCHIVE_ROOT": str(tmp_path / "envarch")})
    assert "PO_SECRET_KEY" in r.stdout and "ANOTHERSECRET" not in r.stdout and "SUPERSECRETKEYVALUE" not in r.stdout
    assert "متغير البيئة PO_ARCHIVE_ROOT" in r.stdout
    r2 = _where([str(prog)])
    assert "instance\\secret.key" in r2.stdout and "SUPERSECRETKEYVALUE" not in r2.stdout


def test_where_lists_what_would_be_left_behind_when_only_config_and_instance_move(tmp_path):
    prog = _program_folder(tmp_path, b"[database]\npath = D:\\PO\\P.accdb\n",
                           extra=[("static/company_logo.png", b"MY-OWN-LOGO"), ("custom_notes.txt", b"mine"), ("serve.py", b"# serve"), ("app.py", b"# CHANGED BY HAND")])
    rel = tmp_path / "release.zip"
    with zipfile.ZipFile(rel, "w") as z:
        z.writestr("po_web/serve.py", "# serve")
        z.writestr("po_web/app.py", "# app")
    r = _where([str(prog), "--release", str(rel)])
    assert "static" in r.stdout and "ليست في الإصدار الجديد" in r.stdout and "(ملفات في الجذر)" in r.stdout      # the logo folder and the loose file
    assert "عُدّلت يدوياً" in r.stdout and "app.py" in r.stdout                                          # a hand-edited program file
    assert not [l for l in r.stdout.splitlines() if l.strip().startswith("[!!]") and "config.ini" in l]       # config.ini is the one thing you DO move: never listed as left behind


def test_where_in_test_mode_fails_when_any_path_points_outside_the_test_folder(tmp_path):
    test_root = tmp_path / "PO_TEST"
    prog = _program_folder(test_root, ("[database]\npath = %s\n[storage]\narchive_root = %s\nlegacy_uploads = %s\nbackup_dir = %s\n[server]\nlog_dir = %s\n" % (
        test_root / "P.accdb", test_root / "archive", test_root / "uploads", test_root / "bk", test_root / "logs")).encode("utf-8"))
    ok = _where([str(prog), "--must-be-inside", str(test_root)])
    assert ok.returncode == 0 and "التثبيت معزول" in ok.stdout
    (prog / "config.ini").write_text("[database]\npath = %s\n[storage]\narchive_root = %s\n" % (test_root / "P.accdb", tmp_path / "PRODUCTION_ARCHIVE"))
    bad = _where([str(prog), "--must-be-inside", str(test_root)])
    assert bad.returncode == 1 and "خارج" in bad.stdout and "ليس معزولاً" in bad.stdout
    env = _where([str(prog), "--must-be-inside", str(test_root)], {"PO_DB_PATH": str(tmp_path / "PRODUCTION.accdb")})
    assert env.returncode == 1 and "متغير البيئة PO_DB_PATH" in env.stdout

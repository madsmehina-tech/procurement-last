# -*- coding: utf-8 -*-
"""v27.6 - the archive, the combined backup (database + archive), the restore into new places, the production guard and the acceptance checks.
(The checks that need the REAL Access engine can only be proven by running tools\\acceptance_check.py on the company PC.)"""
import gzip
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta

import pytest

import conftest
import fake_db
import storage_tools as st


def make_archive(root, n=30, months=14):
    """A pretend archive: n files spread over `months` months (the file times are set so the growth maths has something to read)."""
    for i in range(n):
        d = os.path.join(root, "الموردين" if i % 2 else "المقاولين", "مشروع %d" % (i % 3), "2026")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "ملف %d.pdf" % i)
        with open(p, "wb") as f:
            f.write(b"%PDF-1.4 " + os.urandom(2000 + i * 100))
        t = (datetime.now() - timedelta(days=30 * (i % months))).timestamp()
        os.utime(p, (t, t))
    return root


def make_db(path, size=5000):
    with open(path, "wb") as f:
        f.write(b"ACCESSDB" + os.urandom(size))
    return path


# ---------------------------------------------------------------- the guard
def test_the_guard_refuses_production_paths_inside_and_around(tmp_path):
    prod_db, prod_arch = str(tmp_path / "PO" / "P.accdb"), str(tmp_path / "PO_Archive")
    os.makedirs(prod_arch)
    prot = [prod_db, prod_arch]
    st.guard_outputs([str(tmp_path / "elsewhere")], prot)                                    # fine
    for bad in (prod_arch, os.path.join(prod_arch, "sub"), str(tmp_path), prod_db):          # the folder itself, inside it, a PARENT of it, the db file
        with pytest.raises(st.ProductionGuard):
            st.guard_outputs([bad], prot)
    with pytest.raises(st.ProductionGuard):
        st.guard_sources_are_copies([prod_arch], prot)
    st.guard_sources_are_copies([str(tmp_path / "copy")], prot)


def test_production_paths_come_from_the_ini_file_and_po_protect(tmp_path, monkeypatch):
    import config
    ini = tmp_path / "real.ini"
    a, b, c = str(tmp_path / "PO" / "P.accdb"), str(tmp_path / "PO_Archive"), str(tmp_path / "bk")
    ini.write_text("[database]\npath = %s\n[storage]\narchive_root = %s\nbackup_dir = %s\n" % (a, b, c), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_FILE", str(ini))
    monkeypatch.setenv("PO_PROTECT", "Z:\\other;Y:\\more")
    got = st.production_paths()
    assert {a, b, c, "Z:\\other", "Y:\\more"} <= set(got)


def test_the_backup_cannot_write_into_the_archive_it_copies(tmp_path):
    arch = make_archive(str(tmp_path / "arch"), 3)
    db = make_db(str(tmp_path / "p.accdb"))
    with pytest.raises(st.ProductionGuard):
        st.backup_set(db, arch, os.path.join(arch, "backup"))


# ---------------------------------------------------------------- scan (read only)
def test_scan_reads_sizes_types_months_and_projects_growth(tmp_path):
    arch = make_archive(str(tmp_path / "a"), 40, months=12)
    before = sorted(os.listdir(arch))
    s = st.scan_archive(arch)
    assert s["files"] == 40 and s["bytes"] > 80000 and s["by_extension"]["pdf"]["files"] == 40
    assert len(s["by_month"]) >= 11 and s["growth"]["avg_monthly_bytes"] > 0
    assert s["growth"]["after_5_years"] > s["growth"]["after_1_year"] > s["bytes"]
    assert sorted(os.listdir(arch)) == before and not any(f.endswith(".part") for _r, _d, fs in os.walk(arch) for f in fs)       # read-only
    assert "حجم الأرشيف" in st.scan_report_text(s)


# ---------------------------------------------------------------- backup set + restore
def test_backup_then_restore_gives_back_the_same_database_and_files(tmp_path):
    arch = make_archive(str(tmp_path / "a"), 25)
    db = make_db(str(tmp_path / "p.accdb"))
    dest = str(tmp_path / "bk")
    b = st.backup_set(db, arch, dest)
    assert b["archive_files"] == 25 and b["copied"] == 25 and b["db_stable"] and not b["errors"]
    r = st.restore_set(dest, str(tmp_path / "rs" / "r.accdb"), str(tmp_path / "rs" / "arch"), verify="full")
    assert r["ok"] and r["db_ok"] and r["restored"] == 25 and r["hash_checked"] == 25
    assert open(db, "rb").read() == open(str(tmp_path / "rs" / "r.accdb"), "rb").read()
    a, c = st.scan_archive(arch), st.scan_archive(str(tmp_path / "rs" / "arch"))
    assert (a["files"], a["bytes"]) == (c["files"], c["bytes"])


def test_the_second_backup_copies_only_what_is_new_and_nothing_is_deleted(tmp_path):
    arch = make_archive(str(tmp_path / "a"), 10)
    db = make_db(str(tmp_path / "p.accdb"))
    dest = str(tmp_path / "bk")
    st.backup_set(db, arch, dest, now=datetime.now() - timedelta(minutes=5))
    again = st.backup_set(db, arch, dest)
    assert again["copied"] == 0 and again["skipped"] >= 10
    new = os.path.join(arch, "الموردين", "مشروع 0", "2026", "جديد.pdf")
    open(new, "wb").write(b"%PDF-1.4 new")
    os.remove(os.path.join(arch, "المقاولين", "مشروع 0", "2026", "ملف 0.pdf"))               # deleted from the live archive (moved / removed)
    third = st.backup_set(db, arch, dest)
    assert third["copied"] == 1
    assert os.path.isfile(os.path.join(dest, "archive", "المقاولين", "مشروع 0", "2026", "ملف 0.pdf"))   # the mirror keeps it: nothing is ever deleted there


def test_damage_to_the_backup_is_found_by_the_recorded_hash(tmp_path):
    arch = make_archive(str(tmp_path / "a"), 8)
    db = make_db(str(tmp_path / "p.accdb"))
    dest = str(tmp_path / "bk")
    st.backup_set(db, arch, dest)
    victim = os.path.join(dest, "archive", "الموردين", "مشروع 1", "2026", "ملف 1.pdf")
    data = bytearray(open(victim, "rb").read())
    data[100] ^= 0xFF                                                                  # one flipped bit in the mirror (bit rot)
    open(victim, "wb").write(bytes(data))
    r = st.restore_set(dest, str(tmp_path / "rs" / "r.accdb"), str(tmp_path / "rs" / "arch"), verify="full")
    assert not r["ok"] and r["hash_mismatch_count"] == 1


def test_a_missing_file_in_the_backup_is_reported(tmp_path):
    arch = make_archive(str(tmp_path / "a"), 6)
    db = make_db(str(tmp_path / "p.accdb"))
    dest = str(tmp_path / "bk")
    st.backup_set(db, arch, dest)
    os.remove(os.path.join(dest, "archive", "المقاولين", "مشروع 0", "2026", "ملف 0.pdf"))
    r = st.restore_set(dest, str(tmp_path / "rs" / "r.accdb"), str(tmp_path / "rs" / "arch"))
    assert not r["ok"] and r["missing_in_backup_count"] == 1


def test_restore_refuses_to_write_over_production(tmp_path, monkeypatch):
    arch = make_archive(str(tmp_path / "a"), 3)
    db = make_db(str(tmp_path / "p.accdb"))
    dest = str(tmp_path / "bk")
    st.backup_set(db, arch, dest)
    monkeypatch.setattr(st, "production_paths", lambda: [db, arch])
    with pytest.raises(st.ProductionGuard):
        st.restore_set(dest, db, str(tmp_path / "x"))
    with pytest.raises(st.ProductionGuard):
        st.restore_set(dest, str(tmp_path / "ok.accdb"), arch)
    assert os.path.getsize(db) > 5000


def test_a_database_that_changes_while_it_is_copied_is_flagged(tmp_path, monkeypatch):
    arch = make_archive(str(tmp_path / "a"), 2)
    db = make_db(str(tmp_path / "p.accdb"))
    real = st._backup._safe_copy

    def copy_and_touch(src, dest):
        real(src, dest)
        if src == db:
            with open(db, "ab") as f:
                f.write(b"somebody saved")                                           # a save lands during every copy attempt
    monkeypatch.setattr(st._backup, "_safe_copy", copy_and_touch)
    b = st.backup_set(db, arch, str(tmp_path / "bk"))
    assert b["db_stable"] is False


def test_the_archive_is_copied_before_and_after_the_database(tmp_path, monkeypatch):
    arch = make_archive(str(tmp_path / "a"), 2)
    db = make_db(str(tmp_path / "p.accdb"))
    order = []
    real_db, real_mirror = st._stable_copy_of_db, st.mirror_archive
    monkeypatch.setattr(st, "_stable_copy_of_db", lambda *a, **k: (order.append("db"), real_db(*a, **k))[1])
    monkeypatch.setattr(st, "mirror_archive", lambda *a, **k: (order.append("archive"), real_mirror(*a, **k))[1])
    st.backup_set(db, arch, str(tmp_path / "bk"))
    assert order == ["archive", "db", "archive"]                         # old names first, then the database, then whatever appeared / moved meanwhile


def test_old_sets_are_pruned_but_the_mirror_stays(tmp_path):
    arch = make_archive(str(tmp_path / "a"), 3)
    db = make_db(str(tmp_path / "p.accdb"))
    dest = str(tmp_path / "bk")
    for i in range(5):
        st.backup_set(db, arch, dest, keep=2, now=datetime.now() - timedelta(minutes=10 - i))
    assert len(st.list_sets(dest)) == 2 and len(os.listdir(os.path.join(dest, "db"))) == 2
    assert sum(len(f) for _r, _d, f in os.walk(os.path.join(dest, "archive"))) == 3


# ---------------------------------------------------------------- the app's own backup includes the archive when configured
def test_the_automatic_backup_includes_the_archive(tmp_path, monkeypatch):
    import backup
    import config
    arch = make_archive(str(tmp_path / "a"), 4)
    db = make_db(str(tmp_path / "P.accdb"))
    for k, v in (("DB_PATH", db), ("ARCHIVE_ROOT", arch), ("BACKUP_DIR", str(tmp_path / "b1")), ("BACKUP_ARCHIVE_DIR", str(tmp_path / "set")), ("BACKUP_EXTRA_DIR", "")):
        monkeypatch.setattr(config, k, v)
    path = backup.backup_database("auto", with_archive=True)
    assert path and st.list_sets(str(tmp_path / "set"))
    s = backup.status()
    assert s["archive_in_backup"] and s["archive_last"]
    monkeypatch.setattr(config, "BACKUP_ARCHIVE_DIR", "")
    assert any("الأرشيف غير مشمولة" in w for w in backup.status()["warnings"])


# ---------------------------------------------------------------- acceptance logic (with the stand-in database)
@pytest.fixture()
def world():
    conftest.build_schema()
    import config
    return fake_db, config


def test_transactions_check_passes_on_a_database_that_rolls_back(world):
    import acceptance as ac
    db, cfg = world
    r = ac.check_transactions(db, cfg)
    assert r.status == ac.PASS, r.detail
    assert not db.table_exists(ac.PROBE)                                  # the probe table is always removed


def test_transactions_check_fails_when_rollback_does_not_happen(world, monkeypatch):
    import acceptance as ac
    db, cfg = world
    import contextlib

    @contextlib.contextmanager
    def no_rollback():
        yield db.Transaction()                                            # commits whatever happened, even after an error

    monkeypatch.setattr(db, "transaction", no_rollback)
    r = ac.check_transactions(db, cfg)
    assert r.status == ac.FAIL and "تراجع" in r.detail
    assert not db.table_exists(ac.PROBE)


def test_text_check_and_integrity_check(world):
    import acceptance as ac
    db, cfg = world
    assert ac.check_text(db, cfg).status == ac.PASS
    assert ac.check_integrity(db, cfg).status == ac.PASS
    fake_db.execute("INSERT INTO [PurchaseOrders] ([PONumber], [SupplierName], [TotalAmount]) VALUES ('X', 's', -5)")
    r = ac.check_integrity(db, cfg)
    assert r.status == ac.WARN and "orders_with_negative_total" in r.detail


def test_concurrency_check_counts_every_row(world):
    import acceptance as ac
    db, cfg = world
    r = ac.check_concurrency(db, cfg, threads=1, per_thread=40, processes=0)       # (the stand-in database shares ONE connection, so only 1 thread here; Access gets 4 + 2 processes)
    assert r.status == ac.PASS and r.data["rows_found"] == 40


def test_concurrency_check_fails_when_writes_are_lost(world, monkeypatch):
    import acceptance as ac
    db, cfg = world
    def refuses_three(db_, rows, tag):
        for _ in range(rows - 3):
            db_.execute("INSERT INTO [%s] ([V]) VALUES ('x')" % ac.PROBE)                                                # writes what it could ...
        return {"failed": 3, "retried": 0, "sample": "locked"}                                                           # ... and reports 3 refused writes honestly
    monkeypatch.setattr(ac, "writer_main", refuses_three)
    r = ac.check_concurrency(db, cfg, threads=2, per_thread=5, processes=0)
    assert r.status == ac.FAIL and r.data["thread_failures"] == 6 and "locked" in r.detail


def test_lock_retries_are_a_warning_and_a_silent_loss_is_a_failure(world, monkeypatch):
    import acceptance as ac
    db, cfg = world
    monkeypatch.setattr(ac, "writer_main", lambda db_, rows, tag: (db_.execute("INSERT INTO [%s] ([V]) VALUES ('x')" % ac.PROBE) and None) or {"failed": 0, "retried": 2, "sample": "locked once"})
    r = ac.check_concurrency(db, cfg, threads=1, per_thread=1, processes=0)
    assert r.status == ac.WARN and r.data["thread_retries"] == 2 and "إعادة محاولة" in r.detail
    monkeypatch.setattr(ac, "writer_main", lambda db_, rows, tag: {"failed": 0, "retried": 0, "sample": ""})          # claims success but wrote nothing: records vanished
    r2 = ac.check_concurrency(db, cfg, threads=1, per_thread=3, processes=0)
    assert r2.status == ac.FAIL and "بصمت" in r2.detail


def test_the_writer_retries_a_locked_write_and_reports_what_happened():
    import acceptance as ac

    class Flaky:
        def __init__(self):
            self.n = 0

        def execute(self, sql, params=()):
            self.n += 1
            if self.n in (1, 2):
                raise RuntimeError("Could not update; currently locked")
    r = ac.writer_main(Flaky(), 2, "t", retries=3)
    assert r["failed"] == 0 and r["retried"] == 1 and "locked" in r["sample"]
    class Dead:
        def execute(self, sql, params=()):
            raise RuntimeError("always locked")
    import time
    real_sleep = time.sleep
    time.sleep = lambda s: None
    try:
        assert ac.writer_main(Dead(), 2, "t", retries=2)["failed"] == 2
    finally:
        time.sleep = real_sleep


def test_database_check_reports_a_missing_unique_index(world):
    import acceptance as ac
    db, cfg = world
    fake_db.execute("DROP INDEX ux_po_number")
    r = ac.check_database(db, cfg, migrate=False)
    assert r.status == ac.FAIL and "ux_po_number" in r.detail


def test_archive_check_finds_documents_without_files(world, tmp_path):
    import acceptance as ac
    db, cfg = world
    arch = make_archive(str(tmp_path / "a"), 10)
    fake_db.execute("INSERT INTO [Documents] ([EntityType], [EntityKey], [DocType], [StoredName], [OriginalName], [RelPath], [UploadedAt], [IsCurrent]) VALUES ('PO','1','x','a','a',?,?,1)",
                    ("الموردين/مشروع 1/2026/ملف 1.pdf", datetime.now()))
    fake_db.execute("INSERT INTO [Documents] ([EntityType], [EntityKey], [DocType], [StoredName], [OriginalName], [RelPath], [UploadedAt], [IsCurrent]) VALUES ('PO','2','x','a','a',?,?,1)",
                    ("الموردين/غائب/ملف.pdf", datetime.now()))
    r = ac.check_archive(db, cfg, arch)
    assert r.status == ac.FAIL and r.data["documents_missing"] == 1          # 1 of 2 = 50% > 1%
    assert r.data["scan"]["files"] == 10 and r.data["read_mb_per_second"] >= 0


def test_the_backup_restore_check_end_to_end(world, tmp_path):
    import acceptance as ac
    db, cfg = world
    arch = make_archive(str(tmp_path / "a"), 12)
    dbfile = make_db(str(tmp_path / "copy.accdb"))
    counts = lambda path, tables: {t: int(fake_db.scalar("SELECT COUNT(*) FROM [%s]" % t, default=0) or 0) for t in tables}   # the restored file is not a real Access file here
    docs = lambda f: ["الموردين/مشروع 1/2026/ملف 1.pdf", "المقاولين/مشروع 0/2026/ملف 0.pdf"]            # what the copied database points at
    r = ac.check_backup_restore(db, cfg, dbfile, arch, str(tmp_path / "work"), counter=counts, link_reader=docs)
    assert r.status == ac.PASS, r.detail
    assert r.data["second_backup_copied"] == 0 and r.data["restore"]["ok"]


def test_pages_check_runs_the_real_pages_on_the_data(world):
    import acceptance as ac
    db, cfg = world
    r = ac.check_pages(db, cfg, pages=["/", "/orders", "/certificates"], runs=2)
    assert r.status in (ac.PASS, ac.WARN), r.detail
    assert all(p["status"] == 200 for p in r.data["pages"])


def test_the_verdict_never_passes_with_a_required_check_missing_or_failed():
    import acceptance as ac
    ok = [ac.Result(c, c, ac.PASS) for c in ac.REQUIRED]
    assert ac.verdict(ok) == ac.PASS
    assert ac.verdict(ok + [ac.Result("x", "x", ac.WARN)]) == "PASS_WITH_WARNINGS"
    assert ac.verdict(ok + [ac.Result("y", "y", ac.FAIL)]) == ac.FAIL
    assert ac.verdict([r for r in ok if r.cid != "restore"]) == "INCOMPLETE"                       # not run = not a pass
    skipped = [ac.Result(r.cid, r.cid, ac.SKIP if r.cid == "tx" else ac.PASS) for r in ok]
    assert ac.verdict(skipped) == "INCOMPLETE"


# ---------------------------------------------------------------- the command line tool refuses production
def _run(args, env_extra=None):
    env = dict(os.environ, PO_CSRF="0", **(env_extra or {}))
    root = os.path.dirname(conftest.__file__)
    return subprocess.run([sys.executable, os.path.join(root, "..", "tools", "acceptance_check.py")] + args, capture_output=True, text=True, env=env, timeout=120)


def test_the_tool_refuses_when_the_database_is_the_production_one(tmp_path):
    prod = make_db(str(tmp_path / "PROD.accdb"))
    arch = make_archive(str(tmp_path / "PROD_ARCH"), 2)
    r = _run(["--db-copy", prod, "--archive-copy", arch, "--work", str(tmp_path / "work")], {"PO_PROTECT": prod})
    assert r.returncode == 2 and "ممنوع" in r.stdout
    assert not (tmp_path / "work").exists()                                    # nothing was created


def test_the_tool_refuses_a_work_folder_inside_production(tmp_path):
    cp_db = make_db(str(tmp_path / "copy.accdb"))
    cp_arch = make_archive(str(tmp_path / "copy_arch"), 2)
    prod_arch = tmp_path / "PROD_ARCH"
    prod_arch.mkdir()
    r = _run(["--db-copy", cp_db, "--archive-copy", cp_arch, "--work", str(prod_arch / "w")], {"PO_PROTECT": str(prod_arch)})
    assert r.returncode == 2 and "ممنوع" in r.stdout and not (prod_arch / "w").exists()


def test_the_child_process_stops_when_its_configuration_does_not_point_at_the_copies(tmp_path):
    cp_db = make_db(str(tmp_path / "copy.accdb"))
    cp_arch = make_archive(str(tmp_path / "copy_arch"), 2)
    work = tmp_path / "work"
    work.mkdir()
    r = _run(["--child", "--db-copy", cp_db, "--archive-copy", cp_arch, "--work", str(work)])      # no PO_CONFIG pointing at the copies
    assert r.returncode == 2 and "لن يُنفَّذ شيء" in r.stdout


def test_the_child_process_accepts_real_copies_and_does_not_mistake_them_for_production(tmp_path):
    """Regression: the child's own configuration lists the COPIES; it must only compare against the paths the launcher passed (PO_PROTECT)."""
    cp_db = make_db(str(tmp_path / "copy.accdb"))
    cp_arch = make_archive(str(tmp_path / "copy_arch"), 2)
    work = tmp_path / "work"
    r = _run(["--db-copy", cp_db, "--archive-copy", cp_arch, "--work", str(work)], {"PO_PROTECT": str(tmp_path / "somewhere_else_PROD")})
    assert "ProductionGuard" not in r.stdout + r.stderr and "ممنوع" not in r.stdout
    assert "بدأ اختبار القبول" in r.stdout                                      # it got past both guards (then stops only if pyodbc is missing)


def test_restore_never_overwrites_an_existing_file_or_a_non_empty_folder(tmp_path):
    arch = make_archive(str(tmp_path / "a"), 3)
    db = make_db(str(tmp_path / "p.accdb"))
    dest = str(tmp_path / "bk")
    st.backup_set(db, arch, dest)
    victim = make_db(str(tmp_path / "existing.accdb"), 77)
    before = open(victim, "rb").read()
    with pytest.raises(OSError):
        st.restore_set(dest, victim, str(tmp_path / "newdir"))
    assert open(victim, "rb").read() == before
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "x.txt").write_text("keep me")
    with pytest.raises(OSError):
        st.restore_set(dest, str(tmp_path / "new.accdb"), str(tmp_path / "full"))
    assert (tmp_path / "full" / "x.txt").read_text() == "keep me"

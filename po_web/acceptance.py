# -*- coding: utf-8 -*-
"""
acceptance.py - the checks that decide whether this system is fit to run on the REAL Microsoft Access database and archive.

They run ONLY on COPIES (see tools/acceptance_check.py, which refuses the production paths and starts this in a separate process whose
configuration points at the copies). Every check returns (status, detail, data) with status PASS / FAIL / WARN / SKIP.
A check that could not run is SKIP - never PASS. The tool's verdict is PASS only when nothing failed and nothing important was skipped.

The functions take the database module as an argument so the logic can be tested here with a stand-in database; the Access-specific
behaviour (real transactions, locks, text, speed) can only be proven by running the tool on the company PC.
"""
import os
import random
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime

import storage_tools as st

PASS, FAIL, WARN, SKIP = "PASS", "FAIL", "WARN", "SKIP"
PROBE = "_acc_probe"


class Result:
    def __init__(self, cid, title, status, detail="", data=None, seconds=0.0):
        self.cid, self.title, self.status, self.detail, self.data, self.seconds = cid, title, status, detail, data or {}, round(seconds, 2)

    def as_dict(self):
        return {"id": self.cid, "title": self.title, "status": self.status, "detail": self.detail, "data": self.data, "seconds": self.seconds}


def timed(fn):
    def wrap(*a, **k):
        t = time.time()
        try:
            r = fn(*a, **k)
        except Exception as e:                                 # noqa - a crashing check is a FAILED check, with its reason
            r = Result(fn.__name__, fn.__doc__ or fn.__name__, FAIL, "تعطّل الفحص: %s: %s" % (type(e).__name__, " ".join(str(e).split())[:200]))
        r.seconds = round(time.time() - t, 2)
        return r
    wrap.__name__, wrap.__doc__ = fn.__name__, fn.__doc__
    return wrap


# ------------------------------------------------------------------------------------------------------------- 1. environment
@timed
def check_environment(db, config):
    """بيئة التشغيل: ويندوز، بايثون، سائق Access"""
    info = {"platform": sys.platform, "python": sys.version.split()[0], "bits": 64 if sys.maxsize > 2 ** 32 else 32, "app_version": config.APP_VERSION}
    drivers = []
    try:
        import pyodbc
        drivers = [d for d in pyodbc.drivers() if "access" in d.lower()]
    except Exception:                                          # noqa
        pass
    info["access_drivers"] = drivers
    if not drivers:
        return Result("env", check_environment.__doc__, FAIL, "لا يوجد سائق Microsoft Access (ACE) على هذا الجهاز أو pyodbc غير مثبت.", info)
    return Result("env", check_environment.__doc__, PASS, "بايثون %s (%d بت) · السائق: %s" % (info["python"], info["bits"], drivers[0]), info)


# ------------------------------------------------------------------------------------------------------------- 2. the database copy and its migrations
@timed
def check_database(db, config, migrate=True):
    """قاعدة البيانات (نسخة): فتحها، الترحيلات، الفهارس الفريدة، أعداد السجلات"""
    from migrations import runner
    from migrations import m026_unique_numbers as m26
    data = {}
    if not db.table_exists("PurchaseOrders"):
        return Result("db", check_database.__doc__, FAIL, "تعذّر قراءة جدول PurchaseOrders: هل هذه نسخة صحيحة من القاعدة؟")
    pending_before = runner.pending()
    data["pending_before"] = len(pending_before)
    if migrate and pending_before:
        ok = runner.run(backup=False)                         # on the COPY: this is also the test of the migrations on the real engine
        data["migrations_ok"] = bool(ok)
        if not ok:
            return Result("db", check_database.__doc__, FAIL, "الترحيل على نسخة القاعدة لم يكتمل (انظر الملف المرفق).", data)
    data["pending_after"] = len(runner.pending())
    missing = m26.missing()
    data["missing_unique_indexes"] = [n for _t, n, _l in missing]
    counts = {}
    for t in ("PurchaseOrders", "OrderItems", "Suppliers", "Subcontractors", "Contracts", "Certificates", "Payments", "PaymentCycles", "Documents", "LedgerEntries", "AuditLog", "Users"):
        if db.table_exists(t):
            counts[t] = int(db.scalar("SELECT COUNT(*) FROM [%s]" % t, default=0) or 0)
    data["counts"] = counts
    if missing:
        return Result("db", check_database.__doc__, FAIL, "فهارس فريدة ناقصة: %s (يوجد تكرار في البيانات يلزم تصحيحه)" % "، ".join(data["missing_unique_indexes"]), data)
    if data["pending_after"]:
        return Result("db", check_database.__doc__, WARN, "ترحيلات لم تُنفَّذ: %d" % data["pending_after"], data)
    return Result("db", check_database.__doc__, PASS, "القاعدة سليمة: %d أمر شراء، %d مستند، %d مستخلص" % (counts.get("PurchaseOrders", 0), counts.get("Documents", 0), counts.get("Certificates", 0)), data)


# ------------------------------------------------------------------------------------------------------------- 3. transactions on the real engine
def _drop_probe(db):
    if db.table_exists(PROBE):
        db.execute("DROP TABLE [%s]" % PROBE)


@timed
def check_transactions(db, config):
    """المعاملات: التراجع الكامل عند الفشل (هذا ما يحمي الأموال)"""
    _drop_probe(db)
    db.execute("CREATE TABLE [%s] ([ID] AUTOINCREMENT PRIMARY KEY, [V] TEXT(100))" % PROBE)
    data, problems = {}, []
    try:
        class Boom(Exception):
            pass
        try:
            with db.transaction() as tx:
                tx.execute("INSERT INTO [%s] ([V]) VALUES (?)" % PROBE, ("a",))
                tx.execute("INSERT INTO [%s] ([V]) VALUES (?)" % PROBE, ("b",))
                raise Boom()
        except Boom:
            pass
        data["rows_after_failed_transaction"] = n = int(db.scalar("SELECT COUNT(*) FROM [%s]" % PROBE, default=-1))
        if n != 0:
            problems.append("بعد معاملة فاشلة بقي %d سجل (لم يحدث تراجع!)" % n)
        try:                                                  # a statement that the ENGINE rejects in the middle of the work
            with db.transaction() as tx:
                tx.execute("INSERT INTO [%s] ([V]) VALUES (?)" % PROBE, ("c",))
                tx.execute("INSERT INTO [%s] ([NoSuchColumn]) VALUES (?)" % PROBE, ("d",))
        except Exception:                                      # noqa - expected
            pass
        data["rows_after_engine_error"] = n = int(db.scalar("SELECT COUNT(*) FROM [%s]" % PROBE, default=-1))
        if n != 0:
            problems.append("بعد خطأ من المحرك في منتصف المعاملة بقي %d سجل" % n)
        with db.transaction() as tx:
            tx.execute("INSERT INTO [%s] ([V]) VALUES (?)" % PROBE, ("ok",))
            new_id = tx.execute_insert("INSERT INTO [%s] ([V]) VALUES (?)" % PROBE, ("ok2",))
        data["rows_after_commit"] = n = int(db.scalar("SELECT COUNT(*) FROM [%s]" % PROBE, default=-1))
        data["identity_returned"] = bool(new_id)
        if n != 2:
            problems.append("بعد معاملة ناجحة وُجد %d سجل بدل 2" % n)
        if not new_id:
            problems.append("لم يُرجَع رقم السجل الجديد (@@IDENTITY)")
    finally:
        _drop_probe(db)
    if problems:
        return Result("tx", check_transactions.__doc__, FAIL, "؛ ".join(problems), data)
    return Result("tx", check_transactions.__doc__, PASS, "التراجع عند الفشل يعمل، والتثبيت عند النجاح يعمل", data)


# ------------------------------------------------------------------------------------------------------------- 4. concurrency
def writer_main(db, rows, tag):
    """Used by the helper processes: writes `rows` rows, one connection each, and prints how many failed."""
    failed = 0
    for i in range(rows):
        try:
            db.execute("INSERT INTO [%s] ([V]) VALUES (?)" % PROBE, ("%s-%d" % (tag, i),))
        except Exception:                                      # noqa
            failed += 1
            time.sleep(0.05)
    return failed


@timed
def check_concurrency(db, config, threads=4, per_thread=25, processes=2, per_process=25, script=None):
    """الكتابة المتزامنة: عدة خيوط وعمليتان معاً، دون فقد سجل ولا قفل"""
    _drop_probe(db)
    db.execute("CREATE TABLE [%s] ([ID] AUTOINCREMENT PRIMARY KEY, [V] TEXT(100))" % PROBE)
    failures = []
    lock = threading.Lock()

    def work(k):
        f = writer_main(db, per_thread, "t%d" % k)
        if f:
            with lock:
                failures.append(f)
    t0 = time.time()
    try:
        ths = [threading.Thread(target=work, args=(k,)) for k in range(threads)]
        procs = []
        if script and processes:
            for k in range(processes):
                procs.append(subprocess.Popen([sys.executable, script, "--writer", str(per_process), "p%d" % k], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        proc_failed = 0
        for p in procs:
            out, _err = p.communicate(timeout=300)
            try:
                proc_failed += int(out.strip().splitlines()[-1])
            except (ValueError, IndexError):
                proc_failed += per_process
        expected = threads * per_thread + (processes * per_process if script and processes else 0)
        got = int(db.scalar("SELECT COUNT(*) FROM [%s]" % PROBE, default=0))
        data = {"expected_rows": expected, "rows_found": got, "thread_failures": sum(failures), "process_failures": proc_failed, "seconds": round(time.time() - t0, 1),
                "rows_per_second": round(got / max(time.time() - t0, 0.001), 1)}
    finally:
        _drop_probe(db)
    if data["rows_found"] != data["expected_rows"] or data["thread_failures"] or data["process_failures"]:
        return Result("conc", check_concurrency.__doc__, FAIL, "المتوقع %d سجلاً والموجود %d، وأخطاء: خيوط %d، عمليات %d" % (data["expected_rows"], data["rows_found"], data["thread_failures"], data["process_failures"]), data)
    return Result("conc", check_concurrency.__doc__, PASS, "%d سجلاً كُتبت من %d خيوط%s دون فقد (%.0f سجل/ثانية)" % (got, threads, " وعمليتين" if script and processes else "", data["rows_per_second"]), data)


# ------------------------------------------------------------------------------------------------------------- 5. Arabic text
@timed
def check_text(db, config):
    """النص العربي: الكتابة والقراءة بلا فقد (حتى الأرقام الهندية والتشكيل)"""
    _drop_probe(db)
    db.execute("CREATE TABLE [%s] ([ID] AUTOINCREMENT PRIMARY KEY, [V] TEXT(255))" % PROBE)
    samples = ["شركة الهضب للتجارة والمقاولات", "مؤسسة ملتقى الفنون للمقاولات — فرع الرياض", "أمر شراء رقم ١٢٣ / ٢٠٢٦", "كابل نحاس 4×16 مم² «عزل مضاعف»", "ا" * 250, "نَصٌّ بِالتَّشْكِيل"]
    bad = []
    try:
        for s in samples:
            db.execute("INSERT INTO [%s] ([V]) VALUES (?)" % PROBE, (s,))
        back = [r["V"] for r in db.query("SELECT [V] FROM [%s] ORDER BY [ID]" % PROBE)]
        for want, got in zip(samples, back):
            norm_want = want.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))      # the application converts Arabic-Indic digits on purpose
            if got not in (want, norm_want):
                bad.append(want[:30])
    finally:
        _drop_probe(db)
    if bad:
        return Result("text", check_text.__doc__, FAIL, "نصوص تغيّرت عند الحفظ: %s" % " | ".join(bad), {"bad": bad})
    return Result("text", check_text.__doc__, PASS, "%d نصاً عربياً حُفظ وقُرئ كما هو" % len(samples))


# ------------------------------------------------------------------------------------------------------------- 6. data integrity of the copy
@timed
def check_integrity(db, config):
    """سلامة البيانات: تكرار أرقام الأوامر، بنود بلا أوامر، دفعات بلا مستخلصات، مبالغ سالبة"""
    probes = {
        "duplicate_po_numbers": "SELECT COUNT(*) FROM (SELECT [PONumber] FROM [PurchaseOrders] GROUP BY [PONumber] HAVING COUNT(*) > 1)",
        "order_items_without_order": "SELECT COUNT(*) FROM [OrderItems] WHERE [PONumber] NOT IN (SELECT [PONumber] FROM [PurchaseOrders])",
        "payments_without_certificate": "SELECT COUNT(*) FROM [Payments] WHERE [CertificateID] IS NOT NULL AND [CertificateID] NOT IN (SELECT [CertificateID] FROM [Certificates])",
        "negative_payments": "SELECT COUNT(*) FROM [Payments] WHERE [Amount] < 0",
        "orders_with_negative_total": "SELECT COUNT(*) FROM [PurchaseOrders] WHERE [TotalAmount] < 0",
    }
    found, skipped = {}, []
    for name, sql in probes.items():
        try:
            found[name] = int(db.scalar(sql, default=0) or 0)
        except Exception as e:                                 # noqa - a table that does not exist in an old copy
            skipped.append(name)
    bad = {k: v for k, v in found.items() if v}
    if bad:
        return Result("integrity", check_integrity.__doc__, WARN, "ملاحظات في البيانات: " + "، ".join("%s=%d" % kv for kv in bad.items()), {"found": found, "skipped": skipped})
    return Result("integrity", check_integrity.__doc__, PASS, "لا تكرار ولا سجلات يتيمة ولا مبالغ سالبة", {"found": found, "skipped": skipped})


# ------------------------------------------------------------------------------------------------------------- 7. page speed on the copy
PAGES = ["/", "/orders", "/orders/new", "/certificates", "/suppliers", "/items", "/cycles", "/payments", "/archive", "/search?q=حديد"]


@timed
def check_pages(db, config, pages=PAGES, runs=3, slow_ms=3000):
    """سرعة الصفحات الفعلية على نسخة بياناتك (الوسيط من 3 محاولات)"""
    import app as appmod
    admins = [u for u in db.query("SELECT [UserID], [Status], [RoleID] FROM [Users]") if (u.get("Status") or "") == "active"]
    if not admins:
        return Result("pages", check_pages.__doc__, SKIP, "لا يوجد مستخدم نشط في النسخة لتجربة الصفحات.")
    uid = None
    for u in admins:
        r = db.query("SELECT [RoleKey] FROM [Roles] WHERE [RoleID] = ?", (u["RoleID"],)) if u.get("RoleID") else []
        if r and "admin" in (r[0].get("RoleKey") or "").lower():
            uid = u["UserID"]
            break
    uid = uid or admins[0]["UserID"]
    client = appmod.app.test_client()
    with client.session_transaction() as s:
        s["user_id"] = uid
    rows, worst = [], 0
    for url in pages:
        times, status = [], None
        for _ in range(runs):
            t = time.time()
            resp = client.get(url)
            times.append((time.time() - t) * 1000)
            status = resp.status_code
        med = statistics.median(times)
        worst = max(worst, med)
        rows.append({"page": url, "status": status, "median_ms": round(med), "max_ms": round(max(times))})
    errors = [r for r in rows if r["status"] >= 500]
    slow = [r for r in rows if r["median_ms"] > slow_ms]
    data = {"pages": rows, "worst_median_ms": round(worst)}
    if errors:
        return Result("pages", check_pages.__doc__, FAIL, "صفحات أعطت خطأ خادم: " + "، ".join("%s (%d)" % (r["page"], r["status"]) for r in errors), data)
    if slow:
        return Result("pages", check_pages.__doc__, WARN, "صفحات أبطأ من %d مللي ثانية: " % slow_ms + "، ".join("%s (%d)" % (r["page"], r["median_ms"]) for r in slow), data)
    return Result("pages", check_pages.__doc__, PASS, "كل الصفحات أقل من %d مللي ثانية (الأبطأ %d)" % (slow_ms, worst), data)


# ------------------------------------------------------------------------------------------------------------- 8. the archive copy
@timed
def check_archive(db, config, archive_root, sample=3000, reads=200):
    """الأرشيف (نسخة): الحجم والنمو، والمسارات الطويلة، وأن كل مستند في القاعدة له ملف، وسرعة القراءة"""
    scan = st.scan_archive(archive_root)
    data = {"scan": {k: scan[k] for k in ("files", "bytes", "human", "max_path_length", "zero_byte_files", "unreadable_entries", "growth", "free_disk_bytes", "scan_seconds", "oldest", "newest")},
            "by_extension": dict(list(scan["by_extension"].items())[:8])}
    missing, checked = [], 0
    if db.table_exists("Documents"):
        docs = [r["RelPath"] for r in db.query("SELECT [RelPath], [IsCurrent] FROM [Documents]") if r.get("RelPath")]
        random.Random(7).shuffle(docs)
        for rel in docs[:sample]:
            checked += 1
            if not os.path.isfile(os.path.join(archive_root, *rel.replace("\\", "/").split("/"))):
                missing.append(rel)
    data["documents_checked"], data["documents_missing"] = checked, len(missing)
    data["missing_examples"] = missing[:5]
    files = [os.path.join(archive_root, *r.split("/")) for r in list(_sample_paths(archive_root, reads))]
    t0, nbytes = time.time(), 0
    for f in files:
        try:
            with open(f, "rb") as fh:
                nbytes += len(fh.read(1048576))
        except OSError:
            pass
    el = max(time.time() - t0, 0.001)
    data["read_ms_per_file"] = round(el / max(len(files), 1) * 1000, 1)
    data["read_mb_per_second"] = round(nbytes / 1048576.0 / el, 1)
    notes = []
    if missing:
        notes.append("%d من %d مستنداً في القاعدة بلا ملف في الأرشيف (%.1f%%)" % (len(missing), checked, len(missing) * 100.0 / max(checked, 1)))
    over = [k for k in scan if k.startswith("paths_over_")]
    if over and scan[over[0]]:
        notes.append("مسارات تتجاوز 240 حرفاً: %d" % len(scan[over[0]]))
    if scan["unreadable_entries"]:
        notes.append("عناصر تعذّرت قراءتها: %d" % scan["unreadable_entries"])
    free = scan.get("free_disk_bytes")
    if free is not None and free < scan["growth"]["after_1_year"] * 1.5:
        notes.append("المساحة الحرة (%s) قد لا تكفي لنمو سنة مع النسخ الاحتياطي" % st._human(free))
    status = FAIL if (checked and len(missing) / checked > 0.01) else (WARN if notes else PASS)
    return Result("archive", check_archive.__doc__, status, "؛ ".join(notes) or ("%s في %d ملف، القراءة %.1f م.ب/ث" % (scan["human"], scan["files"], data["read_mb_per_second"])), data)


def _sample_paths(root, n):
    allp = [rel for rel, _s, _m in st.walk_files(root) if rel]
    random.Random(3).shuffle(allp)
    return allp[:n]


# ------------------------------------------------------------------------------------------------------------- 9. backup -> restore -> compare
def count_tables_file(path, tables):
    """Row counts of a database FILE opened directly (the restored copy), independent of the application's configuration."""
    import pyodbc
    conn = pyodbc.connect(r"DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};DBQ=%s;" % path, autocommit=True)
    try:
        cur = conn.cursor()
        out = {}
        for t in tables:
            try:
                out[t] = int(cur.execute("SELECT COUNT(*) FROM [%s]" % t).fetchone()[0])
            except Exception:                                  # noqa
                out[t] = None
        return out
    finally:
        conn.close()


@timed
def check_backup_restore(db, config, db_copy, archive_root, work, full_hash=False, counter=count_tables_file, link_reader=st.read_document_paths):
    """النسخ الاحتياطي ثم الاسترجاع على مكان مستقل: القاعدة والأرشيف معاً، مع المقارنة"""
    dest = os.path.join(work, "backup_set")
    out_db = os.path.join(work, "restored", "restored.accdb")
    out_arch = os.path.join(work, "restored", "archive")
    st.guard_outputs([dest, out_db, out_arch])
    b = st.backup_set(db_copy, archive_root, dest, link_reader=link_reader)
    b2 = st.backup_set(db_copy, archive_root, dest, link_reader=link_reader)                      # a second run must copy (almost) nothing: the incremental mirror works
    r = st.restore_set(dest, out_db, out_arch, verify="full" if full_hash else "sample", link_reader=link_reader)
    tables = [t for t in ("PurchaseOrders", "OrderItems", "Suppliers", "Contracts", "Certificates", "Payments", "Documents", "LedgerEntries", "Users") if db.table_exists(t)]
    live = {t: int(db.scalar("SELECT COUNT(*) FROM [%s]" % t, default=0) or 0) for t in tables}
    restored = counter(out_db, tables)
    diffs = {t: (live[t], restored.get(t)) for t in tables if live[t] != restored.get(t)}
    scan_a, scan_b = st.scan_archive(archive_root), st.scan_archive(out_arch)
    data = {"first_backup": {k: b[k] for k in ("archive_files", "archive_bytes", "copied", "seconds", "mb_per_second", "db_stable")},
            "second_backup_copied": b2["copied"], "restore": {k: r[k] for k in ("restored", "hash_checked", "hash_mismatch_count", "missing_in_backup_count", "size_mismatch_count", "seconds", "db_ok", "ok", "links_checked", "links_paths", "links_missing_count")},
            "backup_links": b["links"], "backup_ok": b["ok"],
            "table_count_differences": diffs, "archive_files_original": scan_a["files"], "archive_files_restored": scan_b["files"],
            "archive_bytes_original": scan_a["bytes"], "archive_bytes_restored": scan_b["bytes"], "full_hash": full_hash}
    problems = []
    if not r["ok"]:
        problems.append("الاسترجاع لم يطابق النسخة (قاعدة=%s، ملفات مفقودة=%d، اختلاف بصمة=%d)" % (r["db_ok"], r["missing_in_backup_count"], r["hash_mismatch_count"]))
    if diffs:
        problems.append("اختلاف أعداد السجلات بعد الاسترجاع: %s" % diffs)
    if scan_a["files"] != scan_b["files"] or scan_a["bytes"] != scan_b["bytes"]:
        problems.append("الأرشيف المسترجع يختلف عن الأصلي (%d/%d ملف)" % (scan_b["files"], scan_a["files"]))
    if b2["copied"] > max(5, b["archive_files"] * 0.01):
        problems.append("النسخة الثانية أعادت نسخ %d ملفاً (النسخ التراكمي لا يعمل كما يجب)" % b2["copied"])
    if b["errors"] or b["failed"]:
        problems.append("ملفات/عناصر لم تُنسخ: %d" % b["failed"])
    if not b["ok"]:
        problems.append("النسخة الاحتياطية نفسها غير مكتملة")
    if not r["links_checked"]:
        problems.append("لم تُفحص روابط المستندات في القاعدة المسترجعة (%s)" % r.get("links_error", "غير متاح") + ": الاسترجاع غير مثبت")
    elif r["links_missing_count"]:
        problems.append("%d مستنداً في القاعدة المسترجعة بلا ملف في الأرشيف المسترجع" % r["links_missing_count"])
    if not b["db_stable"]:
        problems.append("ملف القاعدة تغيّر أثناء نسخه (هل النظام يعمل على هذه النسخة؟)")
    if problems:
        return Result("restore", check_backup_restore.__doc__, FAIL, "؛ ".join(problems), data)
    return Result("restore", check_backup_restore.__doc__, PASS, "نُسخت القاعدة و%d ملفاً ثم استُرجعت في %.1f ثانية بمطابقة كاملة للأعداد والأحجام والبصمات%s" % (r["restored"], r["seconds"], " (كل الملفات)" if full_hash else " (عينة)"), data)


# ------------------------------------------------------------------------------------------------------------- the verdict
REQUIRED = ("env", "db", "tx", "conc", "text", "restore")


def verdict(results):
    """PASS only when nothing failed AND every required check really ran and passed (a skipped required check is not a pass)."""
    by = {r.cid: r.status for r in results}
    if any(s == FAIL for s in by.values()):
        return FAIL
    if any(by.get(c) not in (PASS, WARN) for c in REQUIRED):
        return "INCOMPLETE"
    return PASS if not any(s == WARN for s in by.values()) else "PASS_WITH_WARNINGS"


def report_markdown(results, meta, final):
    icon = {PASS: "✅", FAIL: "❌", WARN: "⚠️", SKIP: "⏭️"}
    lines = ["# تقرير اختبار القبول على Access", "", "- التاريخ: %s" % meta["when"], "- نسخة النظام: %s" % meta["app_version"], "- الحكم النهائي: **%s**" % final, ""]
    lines += ["| # | الفحص | النتيجة | التفاصيل | الزمن (ث) |", "|---|---|---|---|---|"]
    for i, r in enumerate(results, 1):
        lines.append("| %d | %s | %s %s | %s | %s |" % (i, r.title, icon.get(r.status, ""), r.status, r.detail.replace("|", "/"), r.seconds))
    return "\n".join(lines) + "\n"


def run_all(db, config, db_copy, archive_root, work, script=None, migrate=True, full_hash=False):
    """Runs every check in order. Returns (results, verdict)."""
    results = [check_environment(db, config), check_database(db, config, migrate=migrate)]
    if results[-1].status == FAIL and results[-1].data.get("pending_after") is None:
        return results, verdict(results)                       # the copy cannot even be read: nothing else is meaningful
    results += [check_transactions(db, config), check_concurrency(db, config, script=script), check_text(db, config), check_integrity(db, config)]
    results.append(check_pages(db, config))
    results.append(check_archive(db, config, archive_root))
    results.append(check_backup_restore(db, config, db_copy, archive_root, work, full_hash=full_hash))
    return results, verdict(results)

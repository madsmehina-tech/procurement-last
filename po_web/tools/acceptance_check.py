# -*- coding: utf-8 -*-
r"""
Acceptance test of the system on the REAL Microsoft Access engine, using COPIES only.

    python tools\acceptance_check.py --db-copy "D:\PO_TEST\PurchaseSystem.accdb" --archive-copy "D:\PO_TEST\archive" --work "D:\PO_TEST\work"

  --full-hash   compares the SHA-256 of EVERY restored file (slow for a big archive; the default checks a random sample of 200)
  --no-migrate  does not run the migrations on the copy first

PROTECTION: the tool refuses to start if the database or archive you give is the production one (or inside it), or if --work is inside
production. The checks run in a SEPARATE process whose configuration is generated in the work folder and points ONLY at the copies, and
that process refuses to continue if its configuration does not. Before and after, the tool records the size and time of the production
database file and the production archive folder and tells you whether they changed (they must not).
Nothing is sent anywhere. The report (acceptance_report.json / .md) is written in the work folder; you may send me those two files.
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _fingerprint(paths):
    out = {}
    for p in paths:
        try:
            st = os.stat(p)
            n = len(os.listdir(p)) if os.path.isdir(p) else None
            out[p] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "entries": n}
        except OSError:
            out[p] = None
    return out


def launcher(args):
    import config
    import storage_tools as st
    prod = st.production_paths()
    db_copy, arch, work = os.path.abspath(args.db_copy), os.path.abspath(args.archive_copy), os.path.abspath(args.work)
    try:
        st.guard_sources_are_copies([db_copy, arch], prod)
        st.guard_outputs([work], prod)
    except st.ProductionGuard as e:
        print("[ممنوع]", e)
        return 2
    if not os.path.isfile(db_copy):
        print("[خطأ] ملف نسخة القاعدة غير موجود:", db_copy)
        return 2
    if not os.path.isdir(arch):
        print("[خطأ] مجلد نسخة الأرشيف غير موجود:", arch)
        return 2
    if st.is_inside(work, db_copy) or st.is_inside(work, arch):
        print("[خطأ] مجلد العمل يجب أن يكون خارج نسخة القاعدة ونسخة الأرشيف.")
        return 2
    os.makedirs(work, exist_ok=True)
    ini = os.path.join(work, "acceptance.ini")
    with open(ini, "w", encoding="utf-8") as f:
        f.write("[database]\npath = %s\n[storage]\narchive_root = %s\nbackup_dir = %s\nbackup_auto_hours = 0\n[server]\nlog_dir = %s\nenvironment = staging\n"
                % (db_copy, arch, os.path.join(work, "own_backups"), os.path.join(work, "logs")))
    env = dict(os.environ, PO_CONFIG=ini, PO_PROTECT=";".join(prod))
    for k in ("PO_DB_PATH", "PO_ARCHIVE_ROOT", "PO_BACKUP_DIR", "PO_BACKUP_EXTRA_DIR", "PO_BACKUP_ARCHIVE_DIR", "PO_UPLOADS_PATH", "PO_LOG_DIR"):
        env.pop(k, None)
    watch = [p for p in prod if p]
    before = _fingerprint(watch)
    print("بدأ اختبار القبول على النسخ فقط. مسارات التشغيل الحقيقية محمية: %d" % len(prod))
    cmd = [sys.executable, os.path.abspath(__file__), "--child", "--db-copy", db_copy, "--archive-copy", arch, "--work", work]
    cmd += (["--full-hash"] if args.full_hash else []) + (["--no-migrate"] if args.no_migrate else [])
    code = subprocess.call(cmd, env=env)
    after = _fingerprint(watch)
    untouched = before == after
    print("\nهل تغيّرت بيانات التشغيل الحقيقية أثناء الاختبار؟ " + ("لا (سليمة)" if untouched else "[!!] نعم - راجع الأمر فوراً"))
    rp = os.path.join(work, "acceptance_report.json")
    if os.path.isfile(rp):
        data = json.load(open(rp, encoding="utf-8"))
        data["production_untouched"] = untouched
        json.dump(data, open(rp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return code if untouched else 3


def child(args):
    import config
    import storage_tools as st
    ok = (st.norm(config.DB_PATH) == st.norm(args.db_copy) and st.norm(config.ARCHIVE_ROOT) == st.norm(args.archive_copy)
          and st.is_inside(config.BACKUP_DIR, args.work) and not config.BACKUP_EXTRA_DIR and not config.BACKUP_ARCHIVE_DIR)
    if not ok:
        print("[توقّف] إعدادات الاختبار لا تشير إلى النسخ فقط؛ لن يُنفَّذ شيء.")
        return 2
    st.guard_sources_are_copies([config.DB_PATH, config.ARCHIVE_ROOT], st.env_protected())
    try:
        import acceptance
        import db
    except ImportError as e:
        print("[توقّف] مكتبة ناقصة (%s). ثبّت المتطلبات: pip install -r requirements.txt" % e)
        return 2
    results, final = acceptance.run_all(db, config, args.db_copy, args.archive_copy, args.work, script=os.path.abspath(__file__), migrate=not args.no_migrate, full_hash=args.full_hash)
    meta = {"when": datetime.now().isoformat(timespec="seconds"), "app_version": config.APP_VERSION}
    icon = {"PASS": "[ OK ]", "FAIL": "[FAIL]", "WARN": "[ !! ]", "SKIP": "[skip]"}
    for r in results:
        print("%s %s - %s (%ss)" % (icon.get(r.status, ""), r.title, r.detail, r.seconds))
    with open(os.path.join(args.work, "acceptance_report.json"), "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "verdict": final, "results": [r.as_dict() for r in results]}, f, ensure_ascii=False, indent=1)
    with open(os.path.join(args.work, "acceptance_report.md"), "w", encoding="utf-8") as f:
        f.write(acceptance.report_markdown(results, meta, final))
    print("\nالحكم النهائي: %s" % final)
    print("التقرير: %s" % os.path.join(args.work, "acceptance_report.md"))
    return 0 if final in ("PASS", "PASS_WITH_WARNINGS") else 1


def writer(rows, tag):
    import acceptance
    import db
    print(acceptance.writer_main(db, rows, tag))
    return 0


if __name__ == "__main__":
    if "--writer" in sys.argv:
        i = sys.argv.index("--writer")
        sys.exit(writer(int(sys.argv[i + 1]), sys.argv[i + 2]))
    ap = argparse.ArgumentParser()
    ap.add_argument("--db-copy", required=True)
    ap.add_argument("--archive-copy", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--full-hash", action="store_true")
    ap.add_argument("--no-migrate", action="store_true")
    ap.add_argument("--child", action="store_true")
    a = ap.parse_args()
    sys.exit(child(a) if a.child else launcher(a))

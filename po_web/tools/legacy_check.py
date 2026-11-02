# -*- coding: utf-8 -*-
r"""
How many documents live ONLY in the old uploads folder (LEGACY_UPLOADS) - outside the current archive?

    python tools\legacy_check.py --db "D:\PO_TEST\PurchaseSystem.accdb" --archive "D:\PO_TEST\archive" --legacy "D:\PO_TEST\uploads"

Read-only. It reads the paths the database points at (Documents and the older single-file columns) and checks, one by one, whether the file is
in the archive folder, only in the old uploads folder, or nowhere. Run it on the COPIES (the tool refuses the production paths; --on-original-readonly
allows it, but Access may then drop a small temporary «.laccdb» file next to the database while it reads).
Result: 0 documents only in the old uploads folder = nothing special to do. More than 0 = pass --legacy-copy to tools\acceptance_check.py, and the
backup (tools\backup_all.py / the automatic backup) already includes that folder.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import storage_tools as st


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def main():
    db, arch, legacy = arg("--db"), arg("--archive"), arg("--legacy")
    if not (db and arch):
        print(__doc__)
        return 2
    if "--on-original-readonly" not in sys.argv:
        try:
            st.guard_sources_are_copies([db, arch] + ([legacy] if legacy else []))
        except st.ProductionGuard as e:
            print("[ممنوع]", e)
            print("(اعمل نسخة أولاً، أو أضف --on-original-readonly لقراءة الأصل قراءةً فقط.)")
            return 2
    if not os.path.isfile(db) or not os.path.isdir(arch):
        print("[خطأ] تأكد من مسار ملف القاعدة ومجلد الأرشيف.")
        return 2
    try:
        by_source = st.read_linked_paths(db)
    except Exception as e:                                         # noqa
        print("[خطأ] تعذّرت قراءة القاعدة (%s). هل سائق Access مثبت؟" % " ".join(str(e).split())[:150])
        return 2
    legacy = legacy if legacy and os.path.isdir(legacy) else None
    total = {"paths": 0, "in_archive": 0, "legacy_only": 0, "missing": 0}
    rows, examples, gone = [], [], []
    for name, paths in by_source.items():
        c = st.classify_links(paths, arch, legacy)
        rows.append((name, c))
        for k in total:
            total[k] += c[k]
        examples += c["legacy_examples"]
        gone += c["missing_examples"]
    print("الروابط التي تشير إليها القاعدة (لكل جدول/عمود):")
    for name, c in rows:
        print("  %-34s %6d رابط · في الأرشيف %6d · في المرفقات القديمة فقط %5d · لا في أي منهما %5d" % (name, c["paths"], c["in_archive"], c["legacy_only"], c["missing"]))
    print("\nالمجموع: %(paths)d رابطاً · في الأرشيف %(in_archive)d · في المرفقات القديمة فقط %(legacy_only)d · مفقود من الاثنين %(missing)d" % total)
    if not legacy:
        print("(لم يُحدَّد مجلد المرفقات القديمة أو غير موجود: كل ما ليس في الأرشيف يُحسب «مفقوداً».)")
    if total["legacy_only"] == 0:
        print("\nالنتيجة: صفر مستند يعتمد على المرفقات القديمة وحدها: لا حاجة لإجراء خاص بها. سجّل هذه النتيجة.")
    else:
        print("\nالنتيجة: %d مستند يعتمد على المرفقات القديمة وحدها (مثل: %s)." % (total["legacy_only"], examples[0] if examples else ""))
        print("الإجراء: النسخة الاحتياطية تشملها تلقائياً. وفي اختبار القبول أضف:  --legacy-copy <نسخة من مجلد المرفقات القديمة>")
    if total["missing"]:
        print("تنبيه: %d رابطاً بلا ملف في أي مكان (مثل: %s). هذا قبل أي نسخ؛ راجعه (الإعدادات ← صحة الأرشيف)." % (total["missing"], gone[0] if gone else ""))
    if "--json" in sys.argv:
        out = arg("--json")
        json.dump({"total": total, "by_source": {n: c for n, c in rows}}, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("حُفظ:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

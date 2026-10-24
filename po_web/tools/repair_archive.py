# -*- coding: utf-8 -*-
r"""
python tools\repair_archive.py            - shows what it WOULD repair (changes nothing)
python tools\repair_archive.py --apply    - repairs it (a backup of the database is made first)
python tools\repair_archive.py --apply --search E:\    - also looks for lost old files anywhere under E:\

Repairs the two kinds of problems tools\check_archive.py reports:
 1. A record whose file is there under a slightly different name: the name on disk had characters this PC's database
    cannot store (Arabic-Indic digits ١٢٣, special symbols), so the record holds a converted name. The file is renamed
    to a storable name and every record pointing at it is updated - nothing is deleted.
 2. A record pointing at a file of the very first versions (contracts/1/..., certificates/1/...): the file is searched
    for by its name in the old uploads folder (and --search); when found it is COPIED into its place in the archive and
    the record updated. Files that cannot be found anywhere are listed: re-upload them from the page shown.
"""
import fnmatch
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import archive
import archive_health
import config
import db


def refs():
    """Every place that points at a file: (where, key, path, how to update it)."""
    out = []
    for d in db.query("SELECT [DocID], [EntityType], [EntityKey], [DocType], [RelPath] FROM [Documents]"):
        out.append({"label": "%s %s - %s" % (d["EntityType"], d["EntityKey"], d["DocType"]), "path": d["RelPath"], "doc": d})
    for table, keycol, cols in archive_health.LEGACY:
        if not db.table_exists(table):
            continue
        for r in db.query("SELECT * FROM [%s]" % table):
            for c in cols:
                if r.get(c):
                    out.append({"label": "%s %s - %s" % (table, r[keycol], c), "path": r[c], "table": table, "keycol": keycol, "key": r[keycol], "col": c})
    return out


def norm(rel):
    return "/".join(x for x in (rel or "").replace("\\", "/").split("/") if x and x != "..")


def same_name(recorded, on_disk):
    """The disk name is the original; the recorded one is what the database could store of it."""
    if archive.storable_filename(on_disk) == archive.storable_filename(recorded):
        return True
    pattern = recorded.replace("[", "[[]").replace("*", "[*]")     # '?' in the record = a character the database could not hold
    return fnmatch.fnmatchcase(on_disk, pattern) or fnmatch.fnmatchcase(archive.storable_filename(on_disk), archive.storable_filename(recorded))


def update_path(old_rel, new_rel, all_refs):
    n = 0
    for r in all_refs:
        if norm(r["path"]) != norm(old_rel):
            continue
        if "doc" in r:
            db.execute("UPDATE [Documents] SET [RelPath] = ?, [StoredName] = ? WHERE [DocID] = ?", (new_rel, os.path.basename(new_rel), r["doc"]["DocID"]))
        else:
            db.execute("UPDATE [%s] SET [%s] = ? WHERE [%s] = ?" % (r["table"], r["col"], r["keycol"]), (new_rel, r["key"]))
        n += 1
    return n


def target_folder(r):
    """Where a found old file belongs in today's archive."""
    if r.get("table") == "Contracts":
        return archive.ensure_contract_folder(int(r["key"]))
    if r.get("table") == "Certificates":
        return archive.cert_doc_folder_rel(archive.ensure_cert_folder(int(r["key"])), "سند الصرف" if r["col"] == "PaymentProofFile" else "المستخلص المعتمد")
    if r.get("table") == "Subcontractors":
        return archive.ensure_sub_folder(int(r["key"]), {"CRFile": "السجل التجاري", "TaxCertFile": "الشهادة الضريبية", "BankCertFile": "شهادة الآيبان"}.get(r["col"], "ملفات إضافية"))
    if r.get("table") == "Suppliers":
        return archive.ensure_supplier_folder(int(r["key"]), {"CRFile": "السجل التجاري", "TaxCertFile": "الشهادة الضريبية", "CatalogFile": "الكتالوج"}.get(r["col"], "ملفات إضافية"))
    if r.get("table") == "PurchaseOrders":
        return archive.po_doc_folder_rel(archive.ensure_po_folder(r["key"]), "عرض السعر" if r["col"] == "QuoteFile" else "أمر الشراء")
    return None


SUPPORTED = ("Contracts", "Certificates", "Subcontractors", "Suppliers", "PurchaseOrders")


def find_matches(base, roots):
    """EVERY file with this name (or its storable form) under the search roots: [absolute path]. A name found twice is not a safe guess."""
    out = []
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if f == base or same_name(base, f):
                    out.append(os.path.join(dirpath, f))
    return out


def main(argv=None):
    argv = sys.argv if argv is None else argv
    apply = "--apply" in argv
    extra = [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "--search"]
    roots = [config.ARCHIVE_ROOT, config.LEGACY_UPLOADS] + extra
    all_refs = refs()
    missing = [r for r in all_refs if not archive.abs_path_exists(r["path"])]
    print("سجلات تشير إلى ملف غير موجود: %d %s" % (len(missing), "" if apply else "(عرض فقط - أضف --apply للتنفيذ)"))
    if apply and missing:
        import backup
        print("نسخة احتياطية للقاعدة:", backup.backup_database("before_repair"))
    fixed, lost, done = 0, [], set()
    for r in missing:
        rel = norm(r["path"])
        if rel in done:
            continue
        folder, name = (rel.rsplit("/", 1) + [""])[:2] if "/" in rel else ("", rel)
        # 1. same folder, name with characters the database could not hold
        try:
            here = os.listdir(archive.abs_path(folder)) if folder else []
        except (OSError, ValueError):
            here = []
        cands = [f for f in here if same_name(name, f) and os.path.isfile(os.path.join(archive.abs_path(folder), f))]
        if len(cands) == 1:
            new_name = archive.storable_filename(cands[0])
            new_rel = folder + "/" + new_name
            print("  [اسم] %s\n         القرص: %s\n         يصبح:  %s" % (r["label"], cands[0], new_name))
            if apply:
                src = os.path.join(archive.abs_path(folder), cands[0])
                dst = os.path.join(archive.abs_path(folder), new_name)
                if os.path.normpath(src) != os.path.normpath(dst):
                    os.replace(src, dst)
                print("         ✔ أُعيدت التسمية وحُدّث %d سجل" % update_path(rel, new_rel, all_refs))
            fixed += 1
            done.add(rel)
            continue
        # 2. an old file of the first versions: look for it by name
        base = os.path.basename(rel)
        matches = find_matches(base, roots)
        if len({os.path.getsize(m) for m in matches}) > 1:                       # two different files with this name: never pick one for the user
            print("  [؟] %s\n         وُجد أكثر من ملف مختلف بهذا الاسم، لن أختار عنك:\n%s" % (r["label"], "\n".join("           " + m for m in matches[:5])))
            lost.append(r)
            continue
        found = matches[0] if matches else None
        if found and r.get("table") in SUPPORTED:
            if not apply:                                                         # the preview touches NOTHING (no folders, no records)
                print("  [قديم] %s\n         وُجد في: %s\n         سيُنسخ إلى أرشيف السجل عند التنفيذ" % (r["label"], found))
                fixed += 1
                done.add(rel)
                continue
            dest_folder = target_folder(r)
            print("  [قديم] %s\n         وُجد في: %s\n         يُنسخ إلى: %s" % (r["label"], found, dest_folder))
            archive.ensure_folder(dest_folder)
            dst = archive._unique(os.path.join(archive.abs_path(dest_folder), archive.storable_filename(os.path.basename(found))))
            shutil.copy2(found, dst)
            print("         ✔ نُسخ وحُدّث %d سجل" % update_path(rel, dest_folder + "/" + os.path.basename(dst), all_refs))
            fixed += 1
            done.add(rel)
            continue
        lost.append(r)
    print("\nقابلة للإصلاح: %d | مفقودة فعلًا: %d" % (fixed, len(lost)))
    for r in lost:
        print("  ✘ %s  (%s) - لا يوجد على القرص بأي اسم: أعد رفعه من صفحته" % (r["label"], r["path"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

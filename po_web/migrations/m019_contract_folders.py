# -*- coding: utf-8 -*-
r"""
m019 - every contract gets its own archive folder; the new certificate entry model; cycle line statuses.

1. Archive:  إدارة مقاولي الباطن \ <المقاول> \ العقود \ عقد رقم XXX \ ملفات العقد
   The signed contract that used to sit in «أوراق المقاول \ العقود» is MOVED there (copy -> check -> update the record ->
   remove the old copy; a missing file is reported and left alone). Papers typed «عقود موقعة» become «ملفات إضافية».
2. Certificates.TotalInclVat / AmountModel: the entry form now takes the certificate value INCLUDING VAT, with no
   retention. Existing certificates keep their old model and their figures exactly as they were.
3. CycleDecisions.StatusCode / Reason / DeferredBefore: each cycle line records ready / hold / stop and why.
Safe to run again.
"""
import os

import archive
import config
import db
from migrations._util import add_column

DESCRIPTION = "مجلد لكل عقد ونموذج المستخلص الجديد وحالات أسطر الدورة"


def schema():
    add_column("Certificates", "TotalInclVat", "DOUBLE")
    add_column("Certificates", "AmountModel", "TEXT(10)")
    add_column("Contracts", "ArchiveFolder", "TEXT(255)")
    add_column("CycleDecisions", "StatusCode", "TEXT(10)")
    add_column("CycleDecisions", "Reason", "TEXT(120)")
    add_column("CycleDecisions", "DeferredBefore", "INTEGER")
    add_column("PaymentCycles", "SentToMgmtAt", "DATETIME")


def contracts():
    moved = missing = made = 0
    for c in db.query("SELECT [ContractID], [ContractFile], [ArchiveFolder] FROM [Contracts]"):
        had = bool(c.get("ArchiveFolder"))
        files = archive.ensure_contract_folder(c["ContractID"])
        made += 0 if had else 1
        rel = c.get("ContractFile")
        if not rel or (c.get("ArchiveFolder") or "").strip() and rel.replace("\\", "/").startswith((c.get("ArchiveFolder") or "") + "/"):
            continue
        already = [d for d in db.query("SELECT [DocID], [RelPath] FROM [Documents] WHERE [EntityType] = ? AND [EntityKey] = ?", ("CONTRACT", str(c["ContractID"])))]
        if already:
            continue
        if not archive.abs_path_exists(rel):
            missing += 1
            print("- contract %s: the signed copy is missing on disk (%s) - left as it is" % (c["ContractID"], rel))
            continue
        doc_id = db.execute_insert(
            "INSERT INTO [Documents] ([EntityType], [EntityKey], [DocType], [StoredName], [OriginalName], [RelPath], [UploadedAt], [UploadedBy], [Note], [IsCurrent]) "
            "VALUES (?, ?, ?, ?, ?, ?, Now(), ?, ?, True)",
            ("CONTRACT", str(c["ContractID"]), "العقد الموقّع", os.path.basename(rel), os.path.basename(rel), rel, "migration", "legacy:ContractFile"))
        if archive.relocate_document(doc_id, files):
            moved += 1
        else:
            db.execute("DELETE FROM [Documents] WHERE [DocID] = ?", (doc_id,))
            missing += 1
    n = 0
    for d in db.query("SELECT [DocID] FROM [Documents] WHERE [EntityType] = ? AND [DocType] = ?", ("SUB", "عقود موقعة")):
        db.execute("UPDATE [Documents] SET [DocType] = ? WHERE [DocID] = ?", ("ملفات إضافية", d["DocID"]))
        n += 1
    for folder, _dirs, _files in os.walk(os.path.join(config.ARCHIVE_ROOT, archive.ROOT_SUBS), topdown=False):
        if os.path.basename(folder) == archive.CONTRACTS_DIR and os.path.basename(os.path.dirname(folder)) == archive.SUB_PAPERS:
            try:
                if not os.listdir(folder):
                    os.rmdir(folder)
            except OSError:
                pass
    print("- contract folders created: %d | signed copies moved: %d | missing: %d | contractor papers re-typed: %d" % (made, moved, missing, n))


def main():
    schema()
    contracts()


if __name__ == "__main__":
    main()

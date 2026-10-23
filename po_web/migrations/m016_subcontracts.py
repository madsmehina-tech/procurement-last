# -*- coding: utf-8 -*-
r"""
m016 - contracts, certificates and payments of the approved design, and the new archive of subcontractors.

1. Payments table (every actual transfer), Contracts.ContractNo, Subcontractors.ArchiveFolder.
2. Certificate statuses -> the design's three: وارد / مراجعة حسابية / توقيعك -> مُدخَل ; مصروفة -> مصروف.
3. Certificates already paid get their payment record (amount, date, voucher), so the payments tab is complete.
4. Files move from  المقاولين  to the new structure, safely (copy -> check -> update the record -> remove the old):
     إدارة مقاولي الباطن / <المقاول> / أوراق المقاول / { السجل التجاري | الشهادات | العقود | ملفات أخرى }
     إدارة مقاولي الباطن / <المقاول> / المستخلصات / <المشروع> / <الموقع> / المستخلص رقم N / { ملف المستخلص | الاعتمادات | الخطابات | المرفقات }
   A file missing on disk is reported and its record left as it is. Empty old folders are removed.
Safe to run again.
"""
import os

import archive
import config
import db
from finance import LEGACY_STATUS, ST_PAID, row_amounts
from migrations._util import add_column

DESCRIPTION = "العقود والمستخلصات والدفعات بالتصميم المعتمد ونقل أرشيف مقاولي الباطن"
SUB_LEGACY = [("CRFile", "السجل التجاري"), ("TaxCertFile", "الشهادة الضريبية"), ("BankCertFile", "شهادة الآيبان"),
              ("QualificationFile", "ملف تأهيلي")]


def schema():
    if not db.table_exists("Payments"):
        db.execute("CREATE TABLE [Payments] ([PaymentID] AUTOINCREMENT PRIMARY KEY, [CertificateID] INTEGER, [SubcontractorID] INTEGER, "
                   "[PaidAt] DATETIME, [Amount] DOUBLE, [BankRef] TEXT(80), [CycleID] INTEGER, [DocID] INTEGER, [Note] TEXT(255), "
                   "[CreatedBy] TEXT(50), [CreatedAt] DATETIME)")
        print("- Payments: created")
    add_column("Contracts", "ContractNo", "TEXT(50)")
    add_column("Subcontractors", "ArchiveFolder", "TEXT(255)")


def statuses():
    n = 0
    for old, new in LEGACY_STATUS.items():
        if old != new:
            before = len(db.query("SELECT [CertificateID] FROM [Certificates] WHERE [Status] = ?", (old,)))
            if before:
                db.execute("UPDATE [Certificates] SET [Status] = ? WHERE [Status] = ?", (new, old))
                n += before
    print("- certificate statuses converted: %d" % n)


def payments():
    have = {p["CertificateID"] for p in db.query("SELECT [CertificateID] FROM [Payments]")}
    contracts = {c["ContractID"]: c for c in db.query("SELECT [ContractID], [SubcontractorID] FROM [Contracts]")}
    proofs = {}
    for d in db.query("SELECT [DocID], [EntityKey], [DocType], [IsCurrent] FROM [Documents] WHERE [EntityType] = ?", ("CERT",)):
        if d["DocType"] == "سند الصرف" and d["IsCurrent"]:
            proofs[str(d["EntityKey"])] = d["DocID"]
    n = 0
    for g in db.query("SELECT * FROM [Certificates]"):
        if g.get("Status") != ST_PAID or g["CertificateID"] in have:
            continue
        total = row_amounts(g)[3]
        db.execute("INSERT INTO [Payments] ([CertificateID], [SubcontractorID], [PaidAt], [Amount], [BankRef], [DocID], [Note], [CreatedBy], [CreatedAt]) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?, Now())",
                   (g["CertificateID"], (contracts.get(g["ContractID"]) or {}).get("SubcontractorID"), g.get("PaymentDate") or g.get("CreatedAt"),
                    total, "", proofs.get(str(g["CertificateID"])), "سُجّل من إصدار سابق", "migration"))
        n += 1
    print("- payments recorded for certificates paid earlier: %d" % n)


def _old(rel):
    return (rel or "").replace("\\", "/").startswith(archive.OLD_ROOT_SUBS + "/")


def files():
    moved = missing = 0
    # contractor papers (the single-file columns) -> Documents rows in the new structure
    done = {(str(d["EntityKey"]), d["Note"]) for d in db.query("SELECT [EntityKey], [Note] FROM [Documents] WHERE [EntityType] = ?", ("SUB",))}
    for s in db.query("SELECT * FROM [Subcontractors]"):
        sid = s["SubcontractorID"]
        archive.ensure_sub_folder(sid)
        for column, doc_type in SUB_LEGACY:
            rel = s.get(column)
            if not rel or (str(sid), "legacy:" + column) in done:
                continue
            if not archive.abs_path_exists(rel):
                missing += 1
                print("- %s / %s: file missing on disk (%s) - left as it is" % (s["Name"], doc_type, rel))
                continue
            doc_id = db.execute_insert(
                "INSERT INTO [Documents] ([EntityType], [EntityKey], [DocType], [StoredName], [OriginalName], [RelPath], [UploadedAt], "
                "[UploadedBy], [Note], [IsCurrent]) VALUES (?, ?, ?, ?, ?, ?, Now(), ?, ?, True)",
                ("SUB", str(sid), doc_type, os.path.basename(rel), os.path.basename(rel), rel, "migration", "legacy:" + column))
            if archive.relocate_document(doc_id, archive.ensure_sub_folder(sid, doc_type)):
                moved += 1
    # signed contracts
    for c in db.query("SELECT [ContractID], [SubcontractorID], [ContractFile] FROM [Contracts]"):
        if c.get("ContractFile") and _old(c["ContractFile"]):
            new = archive.move_file(c["ContractFile"], archive.ensure_sub_folder(c["SubcontractorID"], "عقود موقعة"))
            if new:
                db.execute("UPDATE [Contracts] SET [ContractFile] = ? WHERE [ContractID] = ?", (new, c["ContractID"]))
                moved += 1
            else:
                missing += 1
                print("- contract %s: file missing on disk (%s)" % (c["ContractID"], c["ContractFile"]))
    # certificates: new folder, then every document into its sub-folder
    docs = db.query("SELECT [DocID], [EntityKey], [DocType], [RelPath] FROM [Documents] WHERE [EntityType] = ?", ("CERT",))
    for g in db.query("SELECT [CertificateID], [ArchiveFolder] FROM [Certificates]"):
        if g.get("ArchiveFolder") and not _old(g["ArchiveFolder"]):
            continue
        db.execute("UPDATE [Certificates] SET [ArchiveFolder] = NULL WHERE [CertificateID] = ?", (g["CertificateID"],))
        folder = archive.ensure_cert_folder(g["CertificateID"])
        for d in docs:
            if str(d["EntityKey"]) != str(g["CertificateID"]) or not _old(d["RelPath"]):
                continue
            if archive.relocate_document(d["DocID"], archive.cert_doc_folder_rel(folder, d["DocType"])):
                moved += 1
            else:
                missing += 1
                print("- certificate %s / %s: file missing on disk (%s)" % (g["CertificateID"], d["DocType"], d["RelPath"]))
    # remove the old root's folders that are now empty (never a folder that still holds something)
    old_root = os.path.join(config.ARCHIVE_ROOT, archive.OLD_ROOT_SUBS)
    if os.path.isdir(old_root):
        for folder, dirs, fs in os.walk(old_root, topdown=False):
            try:
                if not os.listdir(folder):
                    os.rmdir(folder)
            except OSError:
                pass
    left = os.path.isdir(old_root)
    print("- files moved to «%s»: %d | missing: %d%s" % (archive.ROOT_SUBS, moved, missing,
          " | some files remain in «%s» (check: python tools\\check_archive.py)" % archive.OLD_ROOT_SUBS if left else ""))


def main():
    schema()
    statuses()
    payments()
    files()


if __name__ == "__main__":
    main()

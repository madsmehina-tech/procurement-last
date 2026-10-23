# -*- coding: utf-8 -*-
"""
archive_health.py - is every file the database knows about really on the disk, and is every file on the disk known?

Electronic archiving only works if both are true. This audit is read-only: it never changes or deletes anything.
  - missing  : a record (Documents row or an old file column) points at a file that is not there
  - orphans  : a file in the archive that no record points at (it exists on disk but would never show in the system)
  - orders   : orders with no archive folder, or whose PDF / data copy was never written
"""
import os

import archive
import config
import db

# (table, key column, file columns) - the single-file columns that older versions kept on the record itself
LEGACY = [
    ("PurchaseOrders", "PONumber", ("QuoteFile", "SignedFile")),
    ("Suppliers", "SupplierID", ("CatalogFile", "QualificationFile", "CRFile", "TaxCertFile")),
    ("Subcontractors", "SubcontractorID", ("CRFile", "TaxCertFile", "BankCertFile", "QualificationFile")),
    ("Contracts", "ContractID", ("ContractFile",)),
    ("Certificates", "CertificateID", ("CertificateFile", "PaymentProofFile")),
]


def _norm(rel):
    return "/".join(x for x in (rel or "").replace("\\", "/").split("/") if x and x != "..")


def _exists(rel):
    parts = _norm(rel).split("/")
    for base in (config.ARCHIVE_ROOT, config.LEGACY_UPLOADS):
        p = os.path.normpath(os.path.join(base, *parts))
        if p.startswith(os.path.normpath(base) + os.sep) and os.path.isfile(p):
            return True
    return False


def scan(orphan_limit=500):
    known, missing = set(), []
    docs = db.query("SELECT [DocID], [EntityType], [EntityKey], [DocType], [RelPath], [IsCurrent] FROM [Documents]")
    for d in docs:
        rel = _norm(d["RelPath"])
        known.add(rel)
        if not _exists(rel):
            missing.append({"where": "المستندات", "what": "%s %s - %s" % (d["EntityType"], d["EntityKey"], d["DocType"]), "path": rel,
                            "current": bool(d.get("IsCurrent"))})
    for table, keycol, cols in LEGACY:
        if not db.table_exists(table):
            continue
        for r in db.query("SELECT [%s], %s FROM [%s]" % (keycol, ", ".join("[%s]" % c for c in cols), table)):
            for c in cols:
                rel = _norm(r.get(c))
                if rel:
                    known.add(rel)
                    if not _exists(rel):
                        missing.append({"where": table, "what": "%s %s - %s" % (table, r[keycol], c), "path": rel, "current": True})
    orphans, total_files = [], 0
    root = os.path.normpath(config.ARCHIVE_ROOT)
    if os.path.isdir(root):
        for folder, dirs, files in os.walk(root):
            if folder == root:
                dirs[:] = [d for d in dirs if not d.startswith("_")]          # _مؤقت, _النظام: the system's own areas
            for f in files:
                total_files += 1
                rel = _norm(os.path.relpath(os.path.join(folder, f), root))
                if rel not in known and len(orphans) < orphan_limit:
                    orphans.append(rel)
    orders = {"no_folder": [], "no_pdf": [], "no_data": []}
    have = {}
    for d in docs:
        if d["EntityType"] == "PO" and d.get("IsCurrent"):
            have.setdefault(d["EntityKey"], set()).add(d["DocType"])
    for o in db.query("SELECT [PONumber], [ArchiveFolder] FROM [PurchaseOrders]"):
        po = o["PONumber"]
        folder = o.get("ArchiveFolder")
        if not folder or not os.path.isdir(os.path.join(root, *_norm(folder).split("/"))):
            orders["no_folder"].append(po)
        if archive.PO_PDF not in have.get(po, ()):
            orders["no_pdf"].append(po)
        if archive.PO_DATA not in have.get(po, ()):
            orders["no_data"].append(po)
    from finance import classify_status
    odd_status = [{"id": g["CertificateID"], "status": g["Status"]} for g in db.query("SELECT [CertificateID], [Status] FROM [Certificates]")
                  if classify_status(g["Status"]) is None] if db.table_exists("Certificates") else []
    return {"odd_status": odd_status, "records": len(docs), "files_on_disk": total_files, "missing": missing, "orphans": orphans, "orders": orders,
            "ok": not missing and not orders["no_folder"]}

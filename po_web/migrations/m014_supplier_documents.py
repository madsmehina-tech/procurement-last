# -*- coding: utf-8 -*-
"""
m014 - moves the old single supplier files (commercial register, tax certificate, catalog, qualification file) into
the new documents list and the new folder structure:

    الموردين / <المورد> / أوراق المورد / { ملف تأهيلي | كتالوج | شهادات | ملفات أخرى }

The ORIGINAL files are COPIED, never moved or deleted, so nothing is lost if you need to go back. Safe to run again
(a file already copied is recognised and skipped).
"""
import os

import archive
import config
import db

DESCRIPTION = "نقل ملفات الموردين القديمة إلى هيكل «أوراق المورد»"
LEGACY = [("CRFile", "السجل التجاري"), ("TaxCertFile", "الشهادة الضريبية"), ("CatalogFile", "الكتالوج"),
          ("QualificationFile", "ملف تأهيلي")]


def _find(rel):
    rel = (rel or "").replace("\\", "/").lstrip("/")
    for base in (config.ARCHIVE_ROOT, config.LEGACY_UPLOADS):
        p = os.path.normpath(os.path.join(base, *[x for x in rel.split("/") if x and x != ".."]))
        if os.path.normpath(p).startswith(os.path.normpath(base) + os.sep) and os.path.isfile(p):
            return p
    return None


def main():
    suppliers = db.query("SELECT * FROM [Suppliers]")
    done = {(d["EntityKey"], d["Note"]) for d in db.query("SELECT [EntityKey], [Note] FROM [Documents] WHERE [EntityType] = ?", ("SUPPLIER",))}
    copied = missing = 0
    for s in suppliers:
        sid = s["SupplierID"]
        archive.ensure_supplier_folder(sid)                          # creates the four sub-folders
        for column, doc_type in LEGACY:
            rel = s.get(column)
            if not rel:
                continue
            marker = "legacy:%s" % column
            if (str(sid), marker) in done:
                continue
            src = _find(rel)
            if not src:
                missing += 1
                print("- %s / %s: the old file is missing on disk (%s) - skipped" % (s["Name"], doc_type, rel))
                continue
            folder = archive.ensure_supplier_folder(sid, doc_type)
            archive.store_document("SUPPLIER", sid, folder, doc_type, src, os.path.basename(src), note=marker,
                                   user="migration", keep_source=True)
            copied += 1
    print("- suppliers: %d | files copied: %d | missing: %d" % (len(suppliers), copied, missing))


if __name__ == "__main__":
    main()

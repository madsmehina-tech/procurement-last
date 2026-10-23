# -*- coding: utf-8 -*-
r"""
m015 - the new structure of an order's archive folder, and where the history of edits is kept.

    <أمر الشراء> / { أمر الشراء | المرفقات | النسخ السابقة }

- Every existing order gets the three sub-folders.
- Files that people uploaded earlier (quote, receipt note, invoice ...) are MOVED into the right sub-folder, safely:
  copied, checked, the database updated, and only then the old copy removed. A file that is missing on disk is
  reported and left alone.
- OrderRevisions: new columns that point to the archived copy of each old version (the old in-database text copy is
  kept and still read for revisions that already exist).
Safe to run again. Afterwards run  python tools\make_po_pdfs.py  once to write the PDF of the orders you already have.
"""
import db
import archive
from migrations._util import add_column

DESCRIPTION = "هيكل مجلد الأمر الجديد وأرشفة نسخ التعديلات"


def main():
    add_column("OrderRevisions", "SnapshotFile", "TEXT(255)")
    add_column("OrderRevisions", "PdfDocID", "INTEGER")
    add_column("OrderRevisions", "DataDocID", "INTEGER")
    moved = missing = already = 0
    orders = db.query("SELECT [PONumber] FROM [PurchaseOrders]")
    for o in orders:
        archive.ensure_po_folder(o["PONumber"])
    for d in db.query("SELECT [DocID], [EntityKey], [DocType], [RelPath] FROM [Documents] WHERE [EntityType] = ?", ("PO",)):
        rel = (d["RelPath"] or "").replace("\\", "/")
        parts = rel.split("/")
        if len(parts) < 2:
            continue
        parent, last = "/".join(parts[:-1]), parts[-2]
        if last in archive.PO_SUBFOLDERS:
            already += 1
            continue
        target = archive.po_doc_folder_rel(parent, d["DocType"])
        if archive.relocate_document(d["DocID"], target):
            moved += 1
        else:
            missing += 1
            print("- the file of document %s (%s) is missing on disk (%s) - left as it is" % (d["DocID"], d["DocType"], rel))
    print("- orders: %d | files moved into the new sub-folders: %d | already in place: %d | missing: %d" % (len(orders), moved, already, missing))


if __name__ == "__main__":
    main()

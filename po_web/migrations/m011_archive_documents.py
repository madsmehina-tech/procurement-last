# -*- coding: utf-8 -*-
"""
m011 - archive: the Documents index table, and the stored archive folder of every purchase order and
certificate (decided once, so renaming a supplier later never orphans its files).
Safe to run again.
"""
import db
from migrations._util import add_column

DESCRIPTION = "فهرس المستندات (Documents) ومجلد الأرشيف لكل أمر شراء ومستخلص"


def main():
    if db.table_exists("Documents"):
        print("- Documents: already exists, unchanged")
    else:
        db.execute(
            "CREATE TABLE [Documents] ("
            "[DocID] AUTOINCREMENT PRIMARY KEY, [EntityType] TEXT(10), [EntityKey] TEXT(80), [DocType] TEXT(40), "
            "[StoredName] TEXT(255), [OriginalName] TEXT(255), [RelPath] TEXT(255), [UploadedAt] DATETIME, "
            "[UploadedBy] TEXT(100), [Note] TEXT(255), [IsCurrent] YESNO)"
        )
        print("- Documents: created")
    add_column("PurchaseOrders", "ArchiveFolder", "TEXT(255)")
    add_column("Certificates", "ArchiveFolder", "TEXT(255)")


if __name__ == "__main__":
    main()

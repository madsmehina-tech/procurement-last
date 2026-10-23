# -*- coding: utf-8 -*-
"""
migrate_suppliers.py - run this ONCE (python migrate_suppliers.py) to add
the new supplier-qualification columns to the existing Suppliers table.

Safe to run again later: it only adds a column if it is not already there,
exactly like modUpgrade.EnsureUpgrade did on the Excel side.
"""
import db

NEW_COLUMNS = [
    ("BankAccount", "TEXT(100)"),
    ("CatalogFile", "TEXT(255)"),
    ("QualificationFile", "TEXT(255)"),
    ("QualificationUpdatedAt", "DATETIME"),
    ("Notes", "MEMO"),
]


def existing_columns():
    conn = db.get_conn()
    try:
        cur = conn.cursor()
        return {row.column_name.lower() for row in cur.columns(table="Suppliers")}
    finally:
        conn.close()


def main():
    if not db.table_exists("Suppliers"):
        print("Suppliers table does not exist yet - run init_db.py first? "
              "(it should already exist from the earlier VBScript setup; "
              "if this prints, tell the person you're working with.)")
        return

    have = existing_columns()
    added = []
    for name, coltype in NEW_COLUMNS:
        if name.lower() in have:
            print(f"- {name}: already exists, unchanged")
            continue
        db.execute(f"ALTER TABLE [Suppliers] ADD COLUMN [{name}] {coltype}")
        added.append(name)
        print(f"- {name}: added")

    print()
    if added:
        print("Done. Added columns:", ", ".join(added))
    else:
        print("Nothing to do - all columns already existed.")


if __name__ == "__main__":
    main()

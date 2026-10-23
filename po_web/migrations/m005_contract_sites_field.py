# -*- coding: utf-8 -*-
"""
restructure_contracts.py - run this ONCE (python restructure_contracts.py):
- Contracts: adds SiteName (the contract no longer carries a value or a
  retention percentage - both are entered per certificate instead; the old
  ContractValue/RetentionPercent columns are left in place, unused, rather
  than risk a DROP COLUMN on a live database).
- Certificates: adds RetentionPercent (moved here from the contract).

Safe to run again later: only adds a column if it is not already there.
"""
import db

CHANGES = [
    ("Contracts", [("SiteName", "TEXT(255)")]),
    ("Certificates", [("RetentionPercent", "DOUBLE")]),
]


def existing_columns(table):
    conn = db.get_conn()
    try:
        cur = conn.cursor()
        return {row.column_name.lower() for row in cur.columns(table=table)}
    finally:
        conn.close()


def main():
    for table, columns in CHANGES:
        if not db.table_exists(table):
            print(f"! {table} does not exist - run the earlier setup scripts first.")
            continue
        have = existing_columns(table)
        for name, coltype in columns:
            if name.lower() in have:
                print(f"- {table}.{name}: already exists, unchanged")
                continue
            db.execute(f"ALTER TABLE [{table}] ADD COLUMN [{name}] {coltype}")
            print(f"- {table}.{name}: added")
    print()
    print("Done.")


if __name__ == "__main__":
    main()

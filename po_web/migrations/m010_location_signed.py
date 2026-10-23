# -*- coding: utf-8 -*-
"""
add_location_signed.py - run this ONCE (python add_location_signed.py) to add:
- PurchaseOrders.Location: the site/location field for purchase orders
  (independent from the subcontractor "ContractSites" concept - its own
  free-text field with its own autocomplete list).
- PurchaseOrders.SignedFile: path to the uploaded signed/approved copy of
  the purchase order (PDF/JPG/PNG), uploaded from the tracking screen after
  all signatures are collected.
Safe to run again later.
"""
import db

COLUMNS = [
    ("Location", "TEXT(255)"),
    ("SignedFile", "TEXT(255)"),
]


def existing_columns():
    conn = db.get_conn()
    try:
        cur = conn.cursor()
        return {row.column_name.lower() for row in cur.columns(table="PurchaseOrders")}
    finally:
        conn.close()


def main():
    if not db.table_exists("PurchaseOrders"):
        print("! PurchaseOrders does not exist - run the earlier setup scripts first.")
        return
    have = existing_columns()
    for name, coltype in COLUMNS:
        if name.lower() in have:
            print(f"- PurchaseOrders.{name}: already exists, unchanged")
            continue
        db.execute(f"ALTER TABLE [PurchaseOrders] ADD COLUMN [{name}] {coltype}")
        print(f"- PurchaseOrders.{name}: added")
    print()
    print("Done.")


if __name__ == "__main__":
    main()

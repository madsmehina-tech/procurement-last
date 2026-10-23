# -*- coding: utf-8 -*-
"""
add_print_fields.py - run this ONCE (python add_print_fields.py) to add the
columns the new printed-PO layout needs: a discount amount, free-form extra
notes/terms rows, and a per-order flag to hide the fixed warehouse-contact
line without erasing its text. Safe to run again later.
"""
import db

COLUMNS = [
    ("Discount", "DOUBLE"),
    ("ExtraNotes", "MEMO"),
    ("HideWarehouseNote", "YESNO"),
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

# -*- coding: utf-8 -*-
"""
add_quote_file.py - run this ONCE (python add_quote_file.py) to add the
QuoteFile column to PurchaseOrders, so the uploaded quote PDF/Word file can
be attached to the order it was read from (shown as a link on the order's
detail page). Safe to run again later.
"""
import db


def main():
    if not db.table_exists("PurchaseOrders"):
        print("! PurchaseOrders does not exist - run the earlier setup scripts first.")
        return
    conn = db.get_conn()
    try:
        cur = conn.cursor()
        have = {row.column_name.lower() for row in cur.columns(table="PurchaseOrders")}
    finally:
        conn.close()
    if "quotefile" in have:
        print("- PurchaseOrders.QuoteFile: already exists, unchanged")
    else:
        db.execute("ALTER TABLE [PurchaseOrders] ADD COLUMN [QuoteFile] TEXT(255)")
        print("- PurchaseOrders.QuoteFile: added")
    print()
    print("Done.")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
test_db.py - run this FIRST (python test_db.py), before init_db.py and
before app.py, to confirm Python can talk to PurchaseSystem.accdb on this
machine at all. Same purpose as test_connection.vbs was for the VBA side:
catch a broken driver/path here, in one small script, instead of inside
the full web app.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sys
import db


def main():
    print("Database path:", db.DB_PATH)
    print("(set the PO_DB_PATH environment variable to point elsewhere)")
    print()
    try:
        n = db.scalar("SELECT COUNT(*) FROM [PurchaseOrders]", default=0)
    except Exception as e:
        print("FAILED to connect / query PurchaseOrders.")
        print("Error:", e)
        print()
        print("Common causes:")
        print("- pyodbc is not installed yet: pip install -r requirements.txt")
        print("- PO_DB_PATH does not point at the real PurchaseSystem.accdb file")
        print("- the 'Microsoft Access Driver (*.mdb, *.accdb)' ODBC driver is")
        print("  missing, or is a different bitness (32/64-bit) than this Python.")
        print("  It normally installs together with the ACE OLEDB engine you")
        print("  already confirmed works from the VBA side.")
        sys.exit(1)

    print("SUCCESS.")
    print("Current PurchaseOrders row count:", n)


if __name__ == "__main__":
    main()

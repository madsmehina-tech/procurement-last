# -*- coding: utf-8 -*-
"""
add_receivers.py - run this ONCE (python add_receivers.py) to create the
Receivers table, which remembers each receiver's name alongside their phone
number so choosing one on the "إنشاء أمر شراء" page can fill in the other
automatically. Safe to run again later.
"""
import db


def main():
    if db.table_exists("Receivers"):
        print("- Receivers: already exists, unchanged")
    else:
        db.execute(
            "CREATE TABLE [Receivers] ("
            "[ReceiverID] AUTOINCREMENT PRIMARY KEY, "
            "[Name] TEXT(255), [Phone] TEXT(50), [UpdatedAt] DATETIME)"
        )
        print("- Receivers: created")
    print()
    print("Done.")


if __name__ == "__main__":
    main()

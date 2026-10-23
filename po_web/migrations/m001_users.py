# -*- coding: utf-8 -*-
"""
init_db.py - run this ONCE (python init_db.py) before starting the server
for the first time. Creates the Users table in the existing
PurchaseSystem.accdb if it does not already exist yet, and creates one
default admin account so you can log in the first time.

Safe to run again later: it does nothing if the table already exists.
"""
import getpass
from werkzeug.security import generate_password_hash
import db


def main():
    if db.table_exists("Users"):
        print("Users table already exists - nothing to do.")
        print("(If you need to add another user, use the 'Add user' page")
        print(" inside the app once logged in, not this script.)")
        return

    print("Creating the Users table in", db.DB_PATH, "...")
    # Every identifier bracketed - see the lesson learned earlier with the
    # Excel-side database script: this avoids any reserved-word surprise.
    db.execute(
        "CREATE TABLE [Users] ("
        "[UserID] AUTOINCREMENT PRIMARY KEY, "
        "[Username] TEXT(50), "
        "[PasswordHash] TEXT(255), "
        "[FullName] TEXT(255), "
        "[Role] TEXT(30), "
        "[CreatedAt] DATETIME)"
    )
    print("Table created.")

    print()
    print("Now create the first (admin) account.")
    username = input("Admin username: ").strip()
    fullname = input("Full name (shown in the app): ").strip() or username
    password = getpass.getpass("Admin password: ")
    password2 = getpass.getpass("Confirm password: ")
    if password != password2:
        print("Passwords did not match - run this script again.")
        return
    if len(password) < 4:
        print("Password is too short - run this script again.")
        return

    db.execute(
        "INSERT INTO [Users] ([Username], [PasswordHash], [FullName], [Role], [CreatedAt]) "
        "VALUES (?, ?, ?, 'admin', Now())",
        (username, generate_password_hash(password), fullname),
    )
    print()
    print("Done. You can now run the server (see README.md) and log in as", username)


if __name__ == "__main__":
    main()

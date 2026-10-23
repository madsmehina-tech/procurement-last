# -*- coding: utf-8 -*-
"""
m013 - the unified project list, order revision history, document expiry dates, supplier archive folder.

- Projects: built from the names already typed on orders and contracts. Spellings that mean the same project
  (extra spaces, أ/إ/ا, ة/ه ...) are unified to the MOST USED spelling, on the orders and contracts too.
- OrderRevisions: one row per saved edit of an order (what it looked like before + what changed).
- Documents.ExpiryDate: optional expiry date of a document (commercial register, certificates ...).
- Suppliers.ArchiveFolder: the supplier's folder, decided once.
Safe to run again.
"""
import db
from migrations._util import add_column
from textnorm import clean_spaces, has_diacritics, norm_key

DESCRIPTION = "قائمة المشاريع الموحّدة وسجل نسخ أوامر الشراء وتواريخ انتهاء المستندات"


def create_tables():
    ddl = {
        "Projects": "CREATE TABLE [Projects] ([ProjectID] AUTOINCREMENT PRIMARY KEY, [ProjectName] TEXT(150), "
                    "[NameKey] TEXT(150), [IsActive] YESNO, [CreatedAt] DATETIME)",
        "OrderRevisions": "CREATE TABLE [OrderRevisions] ([RevID] AUTOINCREMENT PRIMARY KEY, [PONumber] TEXT(40), "
                          "[RevNo] INTEGER, [ChangedAt] DATETIME, [ChangedBy] TEXT(50), [Summary] TEXT(255), [Snapshot] MEMO)",
    }
    for name, sql in ddl.items():
        if db.table_exists(name):
            print("- %s: already exists, unchanged" % name)
        else:
            db.execute(sql)
            print("- %s: created" % name)


def seed_projects():
    existing = {r["NameKey"] or norm_key(r["ProjectName"]) for r in db.query("SELECT [ProjectName], [NameKey] FROM [Projects]")}
    spell = {}                                    # key -> {spelling: how many times used}
    for table in ("PurchaseOrders", "Contracts"):
        for r in db.query("SELECT [Project] FROM [%s]" % table):
            raw = clean_spaces(r["Project"])
            if raw:
                spell.setdefault(norm_key(raw), {})
                spell[norm_key(raw)][raw] = spell[norm_key(raw)].get(raw, 0) + 1
    created = unified = 0
    for key, variants in sorted(spell.items()):
        official = max(variants.items(), key=lambda kv: (kv[1], not has_diacritics(kv[0]), len(kv[0])))[0]   # most used, then WITHOUT tashkeel
        if key not in existing:
            db.execute("INSERT INTO [Projects] ([ProjectName], [NameKey], [IsActive], [CreatedAt]) VALUES (?, ?, True, Now())",
                       (official, key))
            created += 1
        else:
            official = next(r["ProjectName"] for r in db.query("SELECT [ProjectName], [NameKey] FROM [Projects]")
                            if (r["NameKey"] or norm_key(r["ProjectName"])) == key)
        for table in ("PurchaseOrders", "Contracts"):
            for r in db.query("SELECT [Project] FROM [%s]" % table):
                if r["Project"] and norm_key(r["Project"]) == key and r["Project"] != official:
                    db.execute("UPDATE [%s] SET [Project] = ? WHERE [Project] = ?" % table, (official, r["Project"]))
                    unified += 1
                    break                          # the UPDATE already renamed every row with that exact spelling
    print("- projects created: %d | spellings unified: %d" % (created, unified))


def main():
    create_tables()
    add_column("Documents", "ExpiryDate", "DATETIME")
    add_column("Suppliers", "ArchiveFolder", "TEXT(255)")
    seed_projects()


if __name__ == "__main__":
    main()

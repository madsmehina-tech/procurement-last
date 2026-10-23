# -*- coding: utf-8 -*-
"""Small helpers shared by the migrations."""
import db


def existing_columns(table):
    conn = db.get_conn()
    try:
        cur = conn.cursor()
        return {row.column_name.lower() for row in cur.columns(table=table)}
    finally:
        conn.close()


def add_column(table, name, coltype):
    """ALTER TABLE ... ADD COLUMN, only if the column is not there yet. Returns True if it was added."""
    if name.lower() in existing_columns(table):
        print("- %s.%s: already exists, unchanged" % (table, name))
        return False
    db.execute("ALTER TABLE [%s] ADD COLUMN [%s] %s" % (table, name, coltype))
    print("- %s.%s: added" % (table, name))
    return True

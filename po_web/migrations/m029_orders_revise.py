# -*- coding: utf-8 -*-
"""
m029_orders_revise - a separate permission for CHANGING the items and prices of an existing purchase order (orders.revise).

Before, «orders.edit» (update statuses, upload documents) also let a person change prices. To keep every role working exactly as it does today, each role
that has orders.edit receives orders.revise now; the administrator can then untick it for roles that should only update statuses (a store keeper).
"""
import db

DESCRIPTION = "صلاحية «تعديل أصناف وأسعار أمر قائم» منفصلة عن تحديث الحالات"


def main():
    if not db.table_exists("RolePermissions"):
        print("- RolePermissions: not there yet, nothing to grant")
        return
    n = 0
    for r in db.query("SELECT [RoleID] FROM [RolePermissions] WHERE [PermCode] = ?", ("orders.edit",)):
        if not db.query("SELECT [RoleID] FROM [RolePermissions] WHERE [RoleID] = ? AND [PermCode] = ?", (r["RoleID"], "orders.revise")):
            db.execute("INSERT INTO [RolePermissions] ([RoleID], [PermCode]) VALUES (?, ?)", (r["RoleID"], "orders.revise"))
            n += 1
    print("- permission orders.revise granted to the roles that had orders.edit: %d" % n)


if __name__ == "__main__":
    main()

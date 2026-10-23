# -*- coding: utf-8 -*-
"""
m012 - users, roles, permissions, groups and the audit log.

- Users get: Email, JobTitle, Status (active / pending / rejected / disabled), RoleID, AvatarFile, approval details.
- New tables: Roles, RolePermissions, AppGroups, AppUserGroups, AuditLog.
- The five default roles are created (Administrator + 4). Re-running never overwrites permissions you changed.
- Existing accounts are kept and made 'active'. ONE account becomes Administrator (username 'mehina' if it exists,
  otherwise the oldest account); every other existing account becomes 'عرض فقط' (view only) until you give it a role
  from  الإعدادات ← المستخدمون.
Safe to run again.
"""
import db
import permissions
from migrations._util import add_column

DESCRIPTION = "المستخدمون والأدوار والصلاحيات والمجموعات وسجل التدقيق"
ADMIN_USERNAME = "mehina"
ADMIN_JOB_TITLE = "مدير العقود والمشتريات"


def create_tables():
    ddl = {
        "Roles": "CREATE TABLE [Roles] ([RoleID] AUTOINCREMENT PRIMARY KEY, [RoleKey] TEXT(30), [RoleName] TEXT(80), "
                 "[Description] TEXT(200), [IsSystem] YESNO)",
        "RolePermissions": "CREATE TABLE [RolePermissions] ([RoleID] INTEGER, [PermCode] TEXT(60))",
        "AppGroups": "CREATE TABLE [AppGroups] ([GroupID] AUTOINCREMENT PRIMARY KEY, [GroupName] TEXT(80), [Description] TEXT(200))",
        "AppUserGroups": "CREATE TABLE [AppUserGroups] ([UserID] INTEGER, [GroupID] INTEGER)",
        "AuditLog": "CREATE TABLE [AuditLog] ([LogID] AUTOINCREMENT PRIMARY KEY, [LoggedAt] DATETIME, [UserID] INTEGER, "
                    "[Username] TEXT(50), [ActionName] TEXT(40), [EntityType] TEXT(30), [EntityKey] TEXT(80), "
                    "[Details] TEXT(255), [IPAddress] TEXT(45))",
    }
    for name, sql in ddl.items():
        if db.table_exists(name):
            print("- %s: already exists, unchanged" % name)
        else:
            db.execute(sql)
            print("- %s: created" % name)


def add_user_columns():
    for col, typ in (("Email", "TEXT(150)"), ("JobTitle", "TEXT(100)"), ("Status", "TEXT(12)"), ("RoleID", "INTEGER"),
                     ("AvatarFile", "TEXT(255)"), ("ApprovedBy", "TEXT(50)"), ("ApprovedAt", "DATETIME"),
                     ("LastLoginAt", "DATETIME")):
        add_column("Users", col, typ)


def seed_roles():
    """Creates a default role only if its key is missing - an existing role (and its permissions) is never touched."""
    have = {r["RoleKey"] for r in db.query("SELECT [RoleKey] FROM [Roles]")}
    for key, name, desc, perms in permissions.DEFAULT_ROLES:
        if key in have:
            continue
        rid = db.execute_insert(
            "INSERT INTO [Roles] ([RoleKey], [RoleName], [Description], [IsSystem]) VALUES (?, ?, ?, True)",
            (key, name, desc),
        )
        for code in (perms or []):
            db.execute("INSERT INTO [RolePermissions] ([RoleID], [PermCode]) VALUES (?, ?)", (rid, code))
        print("- role created: %s" % name)


def adopt_users():
    roles = {r["RoleKey"]: r["RoleID"] for r in db.query("SELECT [RoleID], [RoleKey] FROM [Roles]")}
    users = sorted(db.query("SELECT [UserID], [Username], [Status], [RoleID], [JobTitle] FROM [Users]"),
                   key=lambda u: u["UserID"])
    if not users:
        print("- no users yet (create the first one with:  python tools\\add_user.py)")
        return
    admin_id = next((u["UserID"] for u in users if (u["Username"] or "").lower() == ADMIN_USERNAME), None)
    if admin_id is None:
        admin_id = users[0]["UserID"]
    has_admin = any(u["RoleID"] == roles[permissions.ADMIN_KEY] for u in users)
    for u in users:
        if not u["Status"]:
            db.execute("UPDATE [Users] SET [Status] = 'active' WHERE [UserID] = ?", (u["UserID"],))
        if not u["RoleID"]:
            if u["UserID"] == admin_id and not has_admin:
                db.execute("UPDATE [Users] SET [RoleID] = ?, [JobTitle] = ? WHERE [UserID] = ?",
                           (roles[permissions.ADMIN_KEY], u["JobTitle"] or ADMIN_JOB_TITLE, u["UserID"]))
                print("- Administrator: %s" % u["Username"])
            else:
                db.execute("UPDATE [Users] SET [RoleID] = ? WHERE [UserID] = ?", (roles["viewer"], u["UserID"]))
                print("- %s: role 'عرض فقط' (change it from الإعدادات ← المستخدمون)" % u["Username"])


def main():
    create_tables()
    add_user_columns()
    seed_roles()
    adopt_users()


if __name__ == "__main__":
    main()

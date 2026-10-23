# -*- coding: utf-8 -*-
r"""
add_user.py - creates an ACTIVE account with a role (use it for the very first account, or when the web
registration page is not an option). Everyone else should use «تسجيل مستخدم جديد» on the sign-in page and be
approved from  الإعدادات ← المستخدمون.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import getpass

from werkzeug.security import generate_password_hash

import db


def main():
    if not db.table_exists("Roles"):
        print("الجداول غير مجهزة بعد - نفّذ:  python migrate.py")
        return
    roles = sorted(db.query("SELECT [RoleID], [RoleName], [RoleKey] FROM [Roles]"), key=lambda r: r["RoleID"])
    rows = db.query("SELECT [Username], [Status], [RoleID] FROM [Users]")
    print("الحسابات الحالية:", len(rows))
    names = {r["RoleID"]: r["RoleName"] for r in roles}
    for r in rows:
        print(" -", r["Username"], "|", r["Status"], "|", names.get(r["RoleID"], "بلا دور"))
    print()
    username = input("اسم المستخدم الجديد (حروف إنجليزية): ").strip()
    if not re.match(r"^[A-Za-z0-9._-]{3,30}$", username) or any(x["Username"] == username for x in rows):
        print("اسم غير صالح أو مستخدم بالفعل.")
        return
    fullname = input("الاسم الكامل: ").strip() or username
    job = input("المسمى الوظيفي (اختياري): ").strip()
    print("\nالأدوار:")
    for i, r in enumerate(roles, 1):
        print("  %d) %s" % (i, r["RoleName"]))
    choice = input("رقم الدور [افتراضي: عرض فقط]: ").strip()
    viewer = next(r for r in roles if r["RoleKey"] == "viewer")
    role = roles[int(choice) - 1] if choice.isdigit() and 1 <= int(choice) <= len(roles) else viewer
    password = getpass.getpass("كلمة المرور (8 أحرف فأكثر، فيها حرف ورقم): ")
    if password != getpass.getpass("أعد كتابتها: "):
        print("كلمتا المرور غير متطابقتين.")
        return
    if len(password) < 8 or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        print("كلمة المرور ضعيفة: 8 أحرف على الأقل وفيها حرف ورقم.")
        return
    db.execute(
        "INSERT INTO [Users] ([Username], [PasswordHash], [FullName], [JobTitle], [Status], [RoleID], [Role], [CreatedAt]) "
        "VALUES (?, ?, ?, ?, 'active', ?, 'user', Now())",
        (username, generate_password_hash(password), fullname, job, role["RoleID"]),
    )
    print("\nتم. يمكنه الدخول باسم %s (الدور: %s)" % (username, role["RoleName"]))


if __name__ == "__main__":
    main()

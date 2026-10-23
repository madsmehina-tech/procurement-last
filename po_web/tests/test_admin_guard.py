# -*- coding: utf-8 -*-
"""Whoever holds «إدارة المستخدمين» must not be able to take over the administrator's account (audit C-10)."""
from datetime import datetime

import pytest
from werkzeug.security import generate_password_hash

import conftest
import fake_db


@pytest.fixture()
def staff_client():
    conftest.build_schema()
    import app as appmod
    import permissions
    admin_role = fake_db.scalar("SELECT [RoleID] FROM [Roles] WHERE [RoleKey] = ?", ("admin",))
    rid = fake_db.execute_insert("INSERT INTO [Roles] ([RoleKey], [RoleName], [Description], [IsSystem]) VALUES ('hr', 'موارد', 'x', 0)")
    fake_db.execute("INSERT INTO [RolePermissions] ([RoleID], [PermCode]) VALUES (?, 'settings.users')", (rid,))
    uid = fake_db.execute_insert("INSERT INTO [Users] ([Username], [PasswordHash], [FullName], [RoleID], [Status], [CreatedAt]) VALUES ('hr1', ?, 'HR', ?, 'active', ?)",
                                 (generate_password_hash("Staff12345"), rid, datetime.now()))
    getattr(permissions, "invalidate_cache", lambda: None)()
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = uid
    admin_id = fake_db.scalar("SELECT [UserID] FROM [Users] WHERE [Username] = 'admin'")
    return c, admin_id, admin_role, rid


def _hash(uid):
    return fake_db.scalar("SELECT [PasswordHash] FROM [Users] WHERE [UserID] = ?", (uid,))


def test_staff_cannot_reset_the_administrator_password(staff_client):
    c, admin_id, _a, _r = staff_client
    before = _hash(admin_id)
    c.post("/settings/users/%d/password" % admin_id, data={"new": "Hacked12345"})
    assert _hash(admin_id) == before


def test_staff_cannot_hand_out_the_administrator_role(staff_client):
    c, admin_id, admin_role, rid = staff_client
    newbie = fake_db.execute_insert("INSERT INTO [Users] ([Username], [PasswordHash], [Status], [CreatedAt]) VALUES ('new1', 'x', 'pending', ?)", (datetime.now(),))
    c.post("/settings/users/%d/approve" % newbie, data={"role_id": admin_role})
    assert fake_db.scalar("SELECT [Status] FROM [Users] WHERE [UserID] = ?", (newbie,)) == "pending"


def test_staff_can_still_manage_ordinary_users(staff_client):
    c, admin_id, admin_role, rid = staff_client
    newbie = fake_db.execute_insert("INSERT INTO [Users] ([Username], [PasswordHash], [Status], [CreatedAt]) VALUES ('new2', 'x', 'pending', ?)", (datetime.now(),))
    c.post("/settings/users/%d/approve" % newbie, data={"role_id": rid})
    assert fake_db.scalar("SELECT [Status] FROM [Users] WHERE [UserID] = ?", (newbie,)) == "active"
    c.post("/settings/users/%d/password" % newbie, data={"new": "Another12345"})
    assert _hash(newbie) != "x"


def test_an_active_account_cannot_be_re_approved_into_admin(staff_client):
    c, admin_id, admin_role, rid = staff_client
    other = fake_db.execute_insert("INSERT INTO [Users] ([Username], [PasswordHash], [RoleID], [Status], [CreatedAt]) VALUES ('u3', 'x', ?, 'disabled', ?)", (rid, datetime.now()))
    c.post("/settings/users/%d/approve" % other, data={"role_id": admin_role})
    assert fake_db.scalar("SELECT [Status] FROM [Users] WHERE [UserID] = ?", (other,)) == "disabled"

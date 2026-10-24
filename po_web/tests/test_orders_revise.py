# -*- coding: utf-8 -*-
"""A store keeper may update an order's statuses but must not change its items and prices (audit M-21)."""
from datetime import datetime

import pytest

import conftest
import fake_db


def _user(perms):
    import auth
    rid = fake_db.execute_insert("INSERT INTO [Roles] ([RoleKey], [RoleName], [Description], [IsSystem]) VALUES (?, 'r', 'x', 0)", ("r%d" % len(perms),))
    for p in perms:
        fake_db.execute("INSERT INTO [RolePermissions] ([RoleID], [PermCode]) VALUES (?, ?)", (rid, p))
    uid = fake_db.execute_insert("INSERT INTO [Users] ([Username], [PasswordHash], [FullName], [RoleID], [Status], [CreatedAt]) VALUES (?, 'x', 'u', ?, 'active', ?)",
                                 ("u%d" % rid, rid, datetime.now()))
    auth.invalidate_roles()
    return uid


@pytest.fixture()
def world():
    conftest.build_schema()
    fake_db.execute("INSERT INTO [PurchaseOrders] ([PONumber], [SupplierName], [TotalAmount], [CreatedAt]) VALUES ('PO-9', 'م', 100, ?)", (datetime.now(),))
    import app as appmod
    return appmod.app


def _client(app, uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = uid
    return c


def test_store_keeper_cannot_open_the_price_editor_but_can_see_the_order(world):
    keeper = _client(world, _user(["orders.view", "orders.edit"]))
    assert keeper.get("/orders/PO-9").status_code == 200
    assert keeper.get("/orders/PO-9/edit").status_code in (302, 403)
    assert "تعديل الأمر" not in keeper.get("/orders/PO-9").get_data(as_text=True)


def test_a_role_with_revise_can_edit(world):
    buyer = _client(world, _user(["orders.view", "orders.edit", "orders.revise"]))
    assert buyer.get("/orders/PO-9/edit").status_code == 200
    assert "تعديل الأمر" in buyer.get("/orders/PO-9").get_data(as_text=True)


def test_the_migration_keeps_every_current_editor_working():
    conftest.build_schema()
    rid = fake_db.execute_insert("INSERT INTO [Roles] ([RoleKey], [RoleName], [Description], [IsSystem]) VALUES ('old', 'قديم', 'x', 0)")
    fake_db.execute("INSERT INTO [RolePermissions] ([RoleID], [PermCode]) VALUES (?, 'orders.edit')", (rid,))
    from migrations import m029_orders_revise
    m029_orders_revise.main()
    m029_orders_revise.main()                                                            # twice: still one row
    assert fake_db.scalar("SELECT COUNT(*) FROM [RolePermissions] WHERE [RoleID] = ? AND [PermCode] = 'orders.revise'", (rid,)) == 1

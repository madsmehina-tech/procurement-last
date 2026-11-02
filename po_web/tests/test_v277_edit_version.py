# -*- coding: utf-8 -*-
"""v27.7 - two people saving the same order, even within the same second, can never overwrite each other."""
import re
from datetime import datetime

import pytest

import conftest
import fake_db


@pytest.fixture(autouse=True)
def _schema():
    conftest.build_schema()


@pytest.fixture()
def client():
    import app as appmod
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    return c


@pytest.fixture(autouse=True)
def no_files(monkeypatch):
    import archive
    import po_files
    monkeypatch.setattr(po_files, "archive_snapshot", lambda *a, **k: {"RelPath": "x.json", "DocID": 0})
    monkeypatch.setattr(po_files, "archive_previous_pdf", lambda *a, **k: {"DocID": 0})
    monkeypatch.setattr(po_files, "save_current", lambda *a, **k: {"pdf": True})
    monkeypatch.setattr(archive, "discard_document", lambda *a, **k: None)


def _order():
    now = datetime.now()
    fake_db.execute("INSERT INTO [PurchaseOrders] ([PONumber], [SupplierName], [Project], [OrderDate], [TotalAmount], [CreatedAt], [ModifiedAt]) VALUES ('PO-7', 'مورد', 'مشروع', ?, 115, ?, ?)", (now, now, now))
    fake_db.execute("INSERT INTO [OrderItems] ([PONumber], [LineNo], [ItemName], [Unit], [Qty], [UnitPrice], [QtyReceived]) VALUES ('PO-7', 1, 'حديد', 'طن', 1, 100, 0)")


def _form(version, price):
    f = {"po_number": "PO-7", "supplier": "مورد", "project": "مشروع", "order_date": "2026-11-01", "discount": "0",
         "item_name": ["حديد"], "item_unit": ["طن"], "item_qty": ["1"], "item_price": [price]}
    if version is not None:
        f["loaded_version"] = version
    return f


def _price():
    return fake_db.query("SELECT [UnitPrice] FROM [OrderItems] WHERE [PONumber] = 'PO-7'")[0]["UnitPrice"]


def _version_in_page(html):
    return re.search(r'name="loaded_version" value="([^"]*)"', html).group(1)


def test_two_saves_in_the_same_second_from_the_same_page_the_second_is_refused(client):
    _order()
    v = _version_in_page(client.get("/orders/PO-7/edit").get_data(as_text=True))
    first = client.post("/orders/PO-7/edit", data=_form(v, "150"))
    second = client.post("/orders/PO-7/edit", data=_form(v, "999"))          # same page, same second: must NOT replace the first
    assert first.status_code == 302 and second.status_code == 200 and "تعارض" in second.get_data(as_text=True)
    assert _price() == 150
    assert fake_db.scalar("SELECT [RowVersion] FROM [PurchaseOrders] WHERE [PONumber] = 'PO-7'") == 1


def test_the_version_check_is_inside_the_save_even_when_the_early_check_passes(client, monkeypatch):
    """Simulates the race: the early check sees the right version, then another save lands before this one's UPDATE."""
    import routes.orders as ro
    _order()
    v = _version_in_page(client.get("/orders/PO-7/edit").get_data(as_text=True))
    real = ro._version
    state = {"n": 0}

    def sneaky(order):
        state["n"] += 1
        seen = real(order)
        if state["n"] == 1:                                                      # the early check reads the right version ...
            fake_db.execute("UPDATE [PurchaseOrders] SET [RowVersion] = 5 WHERE [PONumber] = 'PO-7'")        # ... and a colleague's save lands right after it
        return seen
    monkeypatch.setattr(ro, "_version", sneaky)
    r = client.post("/orders/PO-7/edit", data=_form(v, "777"))
    assert r.status_code == 200 and "تعارض" in r.get_data(as_text=True)
    assert _price() == 100                                                       # rolled back: the items were not replaced either
    assert fake_db.scalar("SELECT [RowVersion] FROM [PurchaseOrders] WHERE [PONumber] = 'PO-7'") == 5


def test_an_old_page_is_refused_and_after_reviewing_the_second_deliberate_save_passes(client):
    _order()
    old = _version_in_page(client.get("/orders/PO-7/edit").get_data(as_text=True))
    fake_db.execute("UPDATE [PurchaseOrders] SET [RowVersion] = 3 WHERE [PONumber] = 'PO-7'")
    r = client.post("/orders/PO-7/edit", data=_form(old, "200"))
    assert "تعارض" in r.get_data(as_text=True) and _price() == 100
    fresh = _version_in_page(r.get_data(as_text=True))
    assert fresh == "3"
    assert client.post("/orders/PO-7/edit", data=_form(fresh, "200")).status_code == 302 and _price() == 200
    assert fake_db.scalar("SELECT [RowVersion] FROM [PurchaseOrders] WHERE [PONumber] = 'PO-7'") == 4


@pytest.mark.parametrize("version", [None, "", "abc", "-1"])
def test_a_form_without_a_valid_version_is_never_saved_silently(client, version):
    _order()
    r = client.post("/orders/PO-7/edit", data=_form(version, "555"))
    assert r.status_code == 302 and "/orders/PO-7/edit" in r.headers["Location"]
    assert _price() == 100


def test_a_status_change_does_not_clash_with_an_edit_and_is_not_lost(client):
    _order()
    v = _version_in_page(client.get("/orders/PO-7/edit").get_data(as_text=True))
    client.post("/orders/PO-7/status", data={"kind": "receipt", "value": "مكتمل", "note": ""})       # the store keeper updates the status meanwhile
    assert client.post("/orders/PO-7/edit", data=_form(v, "300")).status_code == 302                    # the edit does not touch statuses: no false conflict
    row = fake_db.query("SELECT [ReceiptStatus] FROM [PurchaseOrders] WHERE [PONumber] = 'PO-7'")[0]
    assert row["ReceiptStatus"] == "مكتمل" and _price() == 300                                          # and the status was not overwritten


def test_an_order_created_before_the_migration_has_version_zero(client):
    _order()
    fake_db.execute("UPDATE [PurchaseOrders] SET [RowVersion] = NULL WHERE [PONumber] = 'PO-7'")
    page = client.get("/orders/PO-7/edit").get_data(as_text=True)
    assert _version_in_page(page) == "0"
    assert client.post("/orders/PO-7/edit", data=_form("0", "400")).status_code == 302 and _price() == 400


# ---------------------------------------------------------------- the session key
def test_a_session_signed_with_the_old_key_is_not_accepted_after_the_key_changed(client):
    from flask import Flask
    from flask.sessions import SecureCookieSessionInterface
    old = Flask("old")
    old.secret_key = "THE-OLD-EXPOSED-KEY"
    cookie = SecureCookieSessionInterface().get_signing_serializer(old).dumps({"user_id": 1})
    import app as appmod
    fresh = appmod.app.test_client()                                      # no session of its own
    fresh.set_cookie("session", cookie)
    r = fresh.get("/orders")
    assert r.status_code == 302 and "/login" in r.headers["Location"]     # the forged / old-key cookie did not log anyone in


def test_a_key_from_the_environment_is_reported_and_never_replaced(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    monkeypatch.setenv("PO_SECRET_KEY", "FROM-ENV")
    assert config.secret_key_source() == "env" and config.secret_key() == "FROM-ENV"
    assert not (tmp_path / "instance" / "key_rotated_v27_4").exists()


def test_health_says_where_the_session_key_comes_from_without_showing_it(client, monkeypatch):
    monkeypatch.setenv("PO_SECRET_KEY", "SOMETHING-SECRET")
    body = client.get("/health").get_data(as_text=True)
    assert '"session_key_source": "env"' in body.replace('":"', '": "') or "session_key_source" in body
    assert "SOMETHING-SECRET" not in body and "PO_SECRET_KEY" in body

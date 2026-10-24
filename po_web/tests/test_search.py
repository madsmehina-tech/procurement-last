# -*- coding: utf-8 -*-
"""The global search: finds across modules, ignores Arabic letter forms, shows only what the person may open."""
from datetime import datetime

import pytest

import conftest
import fake_db
import helpers as h


@pytest.fixture()
def client():
    conftest.build_schema()
    import app as appmod
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    return c


def test_finds_a_contractor_even_with_a_different_alef_form(client):
    h.add_sub("أحمد للمقاولات")
    html = client.get("/search?q=احمد").get_data(as_text=True)
    assert "أحمد للمقاولات" in html and "مقاولو الباطن" in html


def test_finds_an_order_by_its_number_and_a_certificate_by_the_contractor(client):
    fake_db.execute("INSERT INTO [PurchaseOrders] ([PONumber], [SupplierName], [Project], [TotalAmount], [CreatedAt]) VALUES ('PO-2026-777', 'مورد الحديد', 'برج 1', 500, ?)", (datetime.now(),))
    s = h.add_sub("شركة النور")
    h.add_cert(s, 1000, no=5)
    html = client.get("/search?q=777").get_data(as_text=True)
    assert "PO-2026-777" in html
    html = client.get("/search?q=النور").get_data(as_text=True)
    assert "مستخلص رقم 5" in html


def test_empty_and_no_match_are_handled(client):
    assert client.get("/search").status_code == 200
    assert "لا توجد نتائج" in client.get("/search?q=zzzqqq").get_data(as_text=True)


def test_every_page_has_the_search_box(client):
    assert 'role="search"' in client.get("/").get_data(as_text=True)


def test_a_person_without_permission_sees_nothing_of_that_group(client):
    h.add_sub("مقاول سري")
    import auth
    import search_service
    orig = auth.can
    auth.can = lambda perm: perm != "subcontractors.view"
    try:
        groups = search_service.search("سري", lambda *a, **k: "/x")
    finally:
        auth.can = orig
    assert not any(g["title"] == "مقاولو الباطن" for g in groups)

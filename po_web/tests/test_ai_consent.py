# -*- coding: utf-8 -*-
"""A quotation is sent to the outside AI service only when the person ticks the box for THAT upload (audit M-28)."""
from datetime import datetime
from io import BytesIO

import pytest

import conftest
import fake_db


@pytest.fixture()
def client(monkeypatch):
    conftest.build_schema()
    import quote2po
    import quote_read
    calls = []
    monkeypatch.setattr(quote2po, "has_api_key", lambda: True)
    monkeypatch.setattr(quote2po, "ai_extract", lambda path, md: calls.append(path) or {"items": [{"name": "بند", "unit": "م", "qty": 1, "price": 5}], "supplier": ""})
    monkeypatch.setattr(quote_read, "read_offer", lambda *a, **k: {"md": "", "engine": "none", "res": {}, "items": [], "notes": []})
    import app as appmod
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    c.calls = calls
    cid = fake_db.execute_insert("INSERT INTO [Comparisons] ([Title], [Status], [CreatedAt]) VALUES ('مقارنة', 'مفتوحة', ?)", (datetime.now(),))
    c.cid = cid
    return c


def _upload(c, **extra):
    data = {"party_kind": "supplier", "party": "مورد", "quote_file": (BytesIO(b"%PDF-1.4 x"), "offer.pdf")}
    data.update(extra)
    return c.post("/compare/%d/quotes/new" % c.cid, data=data, content_type="multipart/form-data")


def test_without_the_tick_nothing_is_sent_outside(client):
    _upload(client)
    assert client.calls == []


def test_with_the_tick_for_this_upload_it_is_sent(client):
    _upload(client, use_ai="1")
    assert len(client.calls) == 1

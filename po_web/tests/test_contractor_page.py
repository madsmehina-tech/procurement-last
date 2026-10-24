# -*- coding: utf-8 -*-
"""The contractor page: tabs, the «next action» strip, and the five sections still all present."""
import re

import pytest

import conftest
import helpers as h


@pytest.fixture()
def client():
    conftest.build_schema()
    import app as appmod
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    return c


def test_the_page_has_five_tabs_and_five_panes(client):
    sub = h.add_sub()
    html = client.get("/subcontractors/%d" % sub).get_data(as_text=True)
    assert html.count('role="tab"') == 5
    assert sorted(re.findall(r'data-pane="(\w+)"', html)) == ["certificates", "contracts", "data", "ledger", "papers"]
    assert html.count("<div") == html.count("</div>")                     # the panes close properly


def test_next_action_strip_tells_what_is_waiting(client):
    sub = h.add_sub()
    h.add_cert(sub, 5000)
    html = client.get("/subcontractors/%d" % sub).get_data(as_text=True)
    assert "لم يدخل دورة صرف" in html and "لا يوجد عقد ساري" in html


def test_strip_is_calm_when_nothing_waits(client):
    sub = h.add_sub()
    import fake_db
    from datetime import datetime
    fake_db.execute("INSERT INTO [Contracts] ([SubcontractorID], [Project], [Status], [CreatedAt]) VALUES (?, 'م', 'ساري', ?)", (sub, datetime.now()))
    html = client.get("/subcontractors/%d" % sub).get_data(as_text=True)
    assert "لا يوجد إجراء معلّق" in html

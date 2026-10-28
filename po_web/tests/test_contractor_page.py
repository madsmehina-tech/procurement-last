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


def test_strip_reports_only_what_is_really_waiting(client):
    sub = h.add_sub()
    import fake_db
    from datetime import datetime
    fake_db.execute("INSERT INTO [Contracts] ([SubcontractorID], [Project], [Status], [CreatedAt]) VALUES (?, 'م', 'ساري', ?)", (sub, datetime.now()))
    html = client.get("/subcontractors/%d" % sub).get_data(as_text=True)
    strip = re.search(r'<section class="next-bar.*?</section>', html, re.S).group(0)
    assert "لا يوجد عقد ساري" not in strip and "دورة صرف" not in strip            # money and contract items are calm ...
    assert "ينقص" in strip                                                       # ... only the missing papers are still reported


def test_supplier_page_has_tabs_and_a_strip(client):
    import fake_db
    from datetime import datetime
    sid = fake_db.execute_insert("INSERT INTO [Suppliers] ([Name], [CreatedAt]) VALUES ('مورد الأسمنت', ?)", (datetime.now(),))
    html = client.get("/suppliers/%d" % sid).get_data(as_text=True)
    assert html.count('role="tab"') == 4 and 'data-key="sup"' in html
    assert sorted(re.findall(r'data-pane="(\w+)"', html)) == ["account", "data", "orders", "papers"]
    assert html.count("<div") == html.count("</div>")
    assert "الإجراء التالي" in html and "ينقص" in html                       # no papers uploaded yet


def test_the_tab_script_is_generic_and_the_contractor_page_uses_it(client):
    sub = h.add_sub()
    html = client.get("/subcontractors/%d" % sub).get_data(as_text=True)
    assert 'data-key="sub"' in html and "// ---- tabs" not in html
    assert "[data-tab]" in open("static/ui.js", encoding="utf-8").read()

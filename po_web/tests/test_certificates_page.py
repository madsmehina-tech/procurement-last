# -*- coding: utf-8 -*-
"""The certificates page: every filter of the old page is still there, the chips keep their own counts, and the phone layer is wired."""
from datetime import datetime

import pytest

import conftest
import fake_db
import helpers as h


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


def _world():
    a, b = h.add_sub("مقاول أ"), h.add_sub("مقاول ب")
    c1 = h.add_cert(a, 1000, no=1)                                              # مُدخَل
    c2 = h.add_cert(a, 2000, no=2, status="مصروف")
    fake_db.execute_insert("INSERT INTO [Payments] ([CertificateID], [Amount], [PaidAt]) VALUES (?, ?, ?)", (c2, 2000, datetime.now()))
    h.add_cert(b, 500, no=1, status="مرفوض")
    return a, b


def test_all_the_old_filters_are_still_on_the_page(client):
    _world()
    html = client.get("/certificates").get_data(as_text=True)
    for needle in ('name="q"', 'name="sub"', 'name="project"', 'name="date_from"', 'name="date_to"', 'name="status"', "تصدير Excel", "+ مستخلص جديد"):
        assert needle in html, needle


def test_every_status_chip_keeps_its_own_count_when_one_status_is_chosen(client):
    _world()
    html = client.get("/certificates?status=مصروف").get_data(as_text=True)
    import re
    chips = re.findall(r'<a class="chip[^"]*"[^>]*>.*?</a>', html, re.S)
    nums = {re.search(r'badge [a-z]+">([^<]+)<', c).group(1): int(re.search(r"<i>(\d+)</i>", c).group(1)) for c in chips if "badge" in c}
    assert nums["مُدخَل"] == 1 and nums["مصروف"] == 1 and nums["مرفوض"] == 1          # the old page showed 0 for the others once one status was chosen


def test_filtering_by_contractor_and_search_still_works(client):
    a, b = _world()
    html = client.get("/certificates?sub=%d" % b).get_data(as_text=True)
    body = html.split("<tbody>")[1]
    assert "مقاول ب" in body and "مقاول أ" not in body
    html = client.get("/certificates?q=مقاول أ").get_data(as_text=True)
    assert "مقاول أ" in html.split("<tbody>")[1]


def test_the_paid_bar_shows_the_percentage(client):
    _world()
    html = client.get("/certificates?status=مصروف").get_data(as_text=True)
    assert 'style="width:100%;"' in html and "100%" in html


def test_the_phone_layer_is_present_on_every_page(client):
    html = client.get("/certificates").get_data(as_text=True)
    assert 'class="tabbar' in html and "data-open-menu" in html
    js = open("static/ui.js", encoding="utf-8").read()
    assert "data-label" in js and "table.list" in js
    css = open("static/style.css", encoding="utf-8").read()
    assert "table.list.cards" in css and "font-family: 'Cairo'" in css and "fonts/cairo-arabic-400.woff2" in css
    import os
    assert os.path.exists("static/fonts/cairo-arabic-400.woff2") and os.path.exists("static/fonts/Cairo-OFL-License.txt")


def test_the_home_page_has_the_phone_quick_actions_and_the_filter_toggle_exists(client):
    home = client.get("/").get_data(as_text=True)
    assert 'class="quick no-print"' in home and "أمر صرف" in home and "بحث شامل" in home
    page = client.get("/certificates").get_data(as_text=True)
    assert 'data-toggle=".filters"' in page
    shown = client.get("/certificates?q=x").get_data(as_text=True)
    assert 'class="filters show"' in shown                                  # a search in progress keeps the filter bar open on a phone

# -*- coding: utf-8 -*-
"""v25.3: phone-ready pages, no outside fonts, coloured messages, the «needs attention» panel, double-click protection."""
import glob
import os
import re

import pytest

import conftest
import helpers as h
from ui_helpers import flash_kind

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _login(client):
    return client.post("/login", data={"username": "admin", "password": "Admin12345"})


@pytest.fixture()
def client():
    conftest.build_schema()
    import app as appmod
    c = appmod.app.test_client()
    _login(c)
    return c


def test_message_colours():
    assert flash_kind("تم حفظ المستخلص") == "success"
    assert flash_kind("سُجّل صرف على هذا المستخلص") == "success"
    assert flash_kind("لا يجوز التعديل بعد الصرف") == "error"
    assert flash_kind("لم يتم العثور على السجل.") == "error"
    assert flash_kind("تعذر حفظ الملف") == "error"            # starts like a success word but is a failure
    assert flash_kind("المستخلص #2 سُدِّد بالكامل بدفعة أخرى") == "notice"


def test_no_page_loads_anything_from_outside_and_all_full_pages_have_a_viewport():
    for path in glob.glob(os.path.join(ROOT, "templates", "*.html")):
        text = open(path, encoding="utf-8").read()
        assert "fonts.googleapis" not in text and "fonts.gstatic" not in text, path
        if "<head>" in text and "<html" in text and "base.html" not in os.path.basename(path):
            if 'name="viewport"' not in text:
                # printable / standalone sheets may omit it, but never the three main shells
                assert os.path.basename(path) not in ("login.html", "register.html"), path
    for shell in ("base.html", "login.html", "register.html"):
        assert 'name="viewport"' in open(os.path.join(ROOT, "templates", shell), encoding="utf-8").read()


def test_home_page_has_the_attention_panel_and_skip_link(client):
    r = client.get("/")
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "ما يحتاج انتباهك" in html and "skip-link" in html and 'id="main"' in html
    assert "<title>" in html and "إدارة العقود والمشتريات" in html


def test_attention_panel_lists_a_waiting_certificate(client):
    h.add_cert(h.add_sub(), total=5000)
    html = client.get("/").get_data(as_text=True)
    assert "مستخلص مُدخَل ولم يدخل دورة صرف" in html


def test_coloured_message_is_rendered_with_its_role(client):
    with client.session_transaction() as s:
        s["_flashes"] = [("message", "لا يجوز التعديل بعد الصرف")]
    html = client.get("/").get_data(as_text=True)
    assert 'class="flash flash-error"' in html and 'role="alert"' in html


def test_script_and_styles_exist():
    js = open(os.path.join(ROOT, "static", "ui.js"), encoding="utf-8").read()
    assert "nav-toggle" in js and "disabled = true" in js and "defaultPrevented" in js
    css = open(os.path.join(ROOT, "static", "style.css"), encoding="utf-8").read()
    assert ".flash-error" in css and ":focus-visible" in css and "googleapis" not in css


def test_the_menu_has_a_way_back_to_the_home_page(client):
    html = client.get("/orders").get_data(as_text=True)
    assert 'href="/"' in html and "الرئيسية" in html


PAGES = ["/", "/orders", "/orders/new", "/suppliers", "/items", "/compare", "/subcontractors", "/certificates", "/cycles", "/payments", "/archive",
         "/contracts", "/settings/account", "/settings/users", "/settings/roles", "/settings/projects", "/settings/company", "/project-analysis"]


def test_no_page_has_a_broken_html_attribute(client):
    """A quote inside an attribute silently cuts it (this hid the close-confirmation dialog twice). Parse every main page like a browser."""
    from html.parser import HTMLParser
    bad = []

    class P(HTMLParser):
        def handle_starttag(self, tag, attrs):
            for n, _v in attrs:
                if not re.match(r"^[A-Za-z_:@][-A-Za-z0-9_:.]*$", n):
                    bad.append((self.url, tag, n[:40]))
    seen = 0
    for url in PAGES:
        r = client.get(url)
        if r.status_code != 200:
            continue
        seen += 1
        p = P(); p.url = url
        p.feed(r.get_data(as_text=True))
    assert seen >= 8, "too few pages rendered to mean anything"
    assert not bad, bad


def test_home_figures_are_cached_and_a_save_clears_them(client):
    import fake_db
    import ttl_cache
    ttl_cache.clear()
    client.get("/")
    fake_db.CALLS[0] = 0
    client.get("/")
    second = fake_db.CALLS[0]
    ttl_cache.clear()
    fake_db.CALLS[0] = 0
    client.get("/")
    assert second < fake_db.CALLS[0]                        # the cached visit asks the database less
    client.get("/")
    assert "home_contractors" in ttl_cache._STORE
    client.post("/logout")                                  # any non-GET request
    assert "home_contractors" not in ttl_cache._STORE


def test_one_colour_rule_for_every_status():
    from ui_helpers import status_cls
    assert status_cls("مصروف") == "success" and status_cls("تم السداد") == "success" and status_cls("تمت الترسية") == "success"
    assert status_cls("استلام جزئي") == "warning" and status_cls("بانتظار قرار الإدارة") == "warning" and status_cls("ضمن دورة صرف") == "warning"
    assert status_cls("مرفوض") == "danger" and status_cls("مفسوخ") == "danger" and status_cls("ملغاة") == "danger"
    assert status_cls("مسودة") == "neutral" and status_cls("مُدخَل") == "neutral" and status_cls("") == "neutral"

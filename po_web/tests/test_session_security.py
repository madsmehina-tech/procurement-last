# -*- coding: utf-8 -*-
"""Forged requests, redirects after sign-in, and sign-ins that must end when the account changes (audit C-12, M-22, M-23)."""
import re

import pytest

import conftest
import fake_db
import helpers as h

TOKEN = re.compile(r'name="csrf_token" value="([0-9a-f]+)"')


@pytest.fixture()
def app():
    conftest.build_schema()
    import app as appmod
    appmod.app.config["CSRF_ENABLED"] = True
    yield appmod.app
    appmod.app.config["CSRF_ENABLED"] = False


def _login(client, password="Admin12345", next_=None):
    token = TOKEN.search(client.get("/login").get_data(as_text=True)).group(1)
    return client.post("/login" + (("?next=" + next_) if next_ else ""), data={"username": "admin", "password": password, "csrf_token": token})


def _token(client, url="/settings/account"):
    return TOKEN.search(client.get(url).get_data(as_text=True)).group(1)


# ------------------------------------------------------------------ CSRF
def test_a_post_without_the_secret_token_is_refused(app):
    c = app.test_client()
    _login(c)
    sub = h.add_sub()
    r = c.post("/subcontractors/%d/edit" % sub, data={"name": "اسم مسروق"})
    assert r.status_code == 400
    assert fake_db.scalar("SELECT [Name] FROM [Subcontractors] WHERE [SubcontractorID] = ?", (sub,)) != "اسم مسروق"


def test_a_wrong_token_is_refused(app):
    c = app.test_client()
    _login(c)
    sub = h.add_sub()
    assert c.post("/subcontractors/%d/edit" % sub, data={"name": "x", "csrf_token": "0" * 32}).status_code == 400


def test_the_page_carries_the_token_in_every_post_form_and_it_works(app):
    c = app.test_client()
    _login(c)
    sub = h.add_sub("مقاول")
    html = c.get("/subcontractors/%d" % sub).get_data(as_text=True)
    posts = re.findall(r"<form\b[^>]*method=\"post\"", html, flags=re.I)
    assert posts and len(TOKEN.findall(html)) >= len(posts)
    token = TOKEN.search(html).group(1)
    r = c.post("/subcontractors/%d/edit" % sub, data={"name": "اسم جديد", "csrf_token": token})
    assert r.status_code == 302
    assert fake_db.scalar("SELECT [Name] FROM [Subcontractors] WHERE [SubcontractorID] = ?", (sub,)) == "اسم جديد"


def test_search_forms_do_not_leak_the_token_into_the_address(app):
    c = app.test_client()
    _login(c)
    html = c.get("/certificates").get_data(as_text=True)
    get_forms = re.findall(r"<form\b[^>]*method=\"get\"[^>]*>(.*?)</form>", html, flags=re.I | re.S)
    assert get_forms and not any("csrf_token" in f for f in get_forms)


def test_logging_out_needs_the_button_not_a_link(app):
    c = app.test_client()
    _login(c)
    c.get("/logout")                                                     # a link on another site
    assert c.get("/subcontractors").status_code == 200                   # still signed in
    c.post("/logout", data={"csrf_token": _token(c)})
    assert c.get("/subcontractors").status_code == 302                   # now signed out


# ------------------------------------------------------------------ the page after sign-in
@pytest.mark.parametrize("target,expected", [
    ("/orders", "/orders"), ("/orders?q=1", "/orders?q=1"), ("//evil.com", None), ("/\\evil.com", None), ("http://evil.com", None),
    ("javascript:alert(1)", None), ("/orders\r\nSet-Cookie:x=1", None), ("/a b", None), ("", None), (None, None),
])
def test_next_address_stays_on_this_site(target, expected):
    from routes.auth_routes import _safe_next
    assert _safe_next(target) == expected


# ------------------------------------------------------------------ a changed account ends its open sign-ins
def test_resetting_a_password_signs_that_user_out_everywhere(app):
    from datetime import datetime
    from werkzeug.security import generate_password_hash
    victim = app.test_client()
    _login(victim)                                                       # the «admin» user signs in on one device
    uid = fake_db.scalar("SELECT [UserID] FROM [Users] WHERE [Username] = 'admin'")
    assert victim.get("/subcontractors").status_code == 200
    fake_db.execute("INSERT INTO [Users] ([Username], [PasswordHash], [FullName], [RoleID], [Status], [CreatedAt]) VALUES ('boss', ?, 'b', (SELECT [RoleID] FROM [Roles] WHERE [RoleKey] = 'admin'), 'active', ?)",
                    (generate_password_hash("Boss12345"), datetime.now()))
    import auth
    auth.bump_session_version(uid)                                       # what «reset password» / «disable» do
    assert victim.get("/subcontractors").status_code == 302              # the old device is out


def test_changing_my_own_password_keeps_me_in_but_ends_my_other_devices(app):
    phone, laptop = app.test_client(), app.test_client()
    _login(phone)
    _login(laptop)
    laptop.post("/settings/account/password", data={"current": "Admin12345", "new": "Newpass12345", "new2": "Newpass12345", "csrf_token": _token(laptop)})
    assert laptop.get("/subcontractors").status_code == 200              # the device that changed it stays signed in
    assert phone.get("/subcontractors").status_code == 302               # the other device must sign in again

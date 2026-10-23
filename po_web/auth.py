# -*- coding: utf-8 -*-
"""
auth.py - who is signed in, what they may do, and the guard every page goes through.

- current_user() loads the signed-in user once per request (with role and permissions).
- can("orders.edit") answers a permission question (templates use it too, to hide what is not allowed).
- guard(perm) wraps a page; routes.register_all() applies it to EVERY page from permissions.ENDPOINT_PERMS.
"""
import time
from functools import wraps

from flask import g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

import db
import permissions

_MISSING = object()
_ROLE_CACHE = {}          # RoleID -> (expires_at, {"key":..., "name":..., "perms": set()})
ROLE_TTL = 60             # seconds; any role edit also clears it at once


# --------------------------------------------------------------------------- users
USER_COLUMNS = ("[UserID], [Username], [PasswordHash], [FullName], [Email], [JobTitle], [Status], [RoleID], "
                "[AvatarFile]")


def find_user(username):
    rows = db.query("SELECT %s FROM [Users] WHERE [Username] = ?" % USER_COLUMNS, (username,))
    return rows[0] if rows else None


def verify_login(username, password):
    """Returns the user dict if the password is right (whatever the account status - the caller checks it)."""
    user = find_user(username)
    if user and user.get("PasswordHash") and check_password_hash(user["PasswordHash"], password):
        return user
    return None


# --------------------------------------------------------------------------- roles & permissions
def invalidate_roles():
    _ROLE_CACHE.clear()


def load_role(role_id):
    if not role_id:
        return None
    hit = _ROLE_CACHE.get(role_id)
    if hit and hit[0] > time.time():
        return hit[1]
    rows = db.query("SELECT [RoleID], [RoleKey], [RoleName] FROM [Roles] WHERE [RoleID] = ?", (role_id,))
    if not rows:
        return None
    perms = {r["PermCode"] for r in db.query("SELECT [PermCode] FROM [RolePermissions] WHERE [RoleID] = ?", (role_id,))}
    role = {"key": rows[0]["RoleKey"], "name": rows[0]["RoleName"], "perms": perms}
    _ROLE_CACHE[role_id] = (time.time() + ROLE_TTL, role)
    return role


def current_user():
    """The signed-in user (dict with RoleName, IsAdmin, Perms) or None. Cached for the rest of the request."""
    if "user_id" not in session:
        return None
    cached = g.get("_po_user", _MISSING)
    if cached is not _MISSING:
        return cached
    rows = db.query("SELECT %s FROM [Users] WHERE [UserID] = ?" % USER_COLUMNS.replace("[PasswordHash], ", ""),
                    (session["user_id"],))
    user = rows[0] if rows else None
    if user:
        role = load_role(user.get("RoleID"))
        user["RoleName"] = role["name"] if role else ""
        user["IsAdmin"] = bool(role and role["key"] == permissions.ADMIN_KEY)
        user["Perms"] = role["perms"] if role else set()
    g._po_user = user
    return user


def can(code):
    user = current_user()
    if not user or user.get("Status", "active") != "active":
        return False
    return bool(user.get("IsAdmin") or code in user.get("Perms", ()))


def _forbidden():
    return render_template("error.html", code=403, title="لا تملك صلاحية لهذه الصفحة",
                           text="اطلب من مدير النظام إضافة الصلاحية المناسبة لدورك."), 403


def guard(perm):
    """Wraps one page. perm: a permission code | None (any signed-in user) | PUBLIC | a function(**url_args)."""
    def deco(view):
        if perm == permissions.PUBLIC:
            return view

        @wraps(view)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                return redirect(url_for("login", next=request.path))
            user = current_user()
            if not user or user.get("Status", "active") != "active":
                session.clear()
                return redirect(url_for("login"))
            need = perm(**kwargs) if callable(perm) else perm
            if need and not can(need):
                return _forbidden()
            return view(*args, **kwargs)
        return wrapped
    return deco


def login_required(view):
    """Kept for the few places that still use it: any signed-in, active user."""
    return guard(None)(view)


# ---- brute-force throttle: 5 wrong passwords for the same user from the same PC within 10 minutes
# blocks further attempts for 10 minutes (kept in memory; one server process serves everyone).
_FAILS = {}
MAX_FAILS = 5
WINDOW_SECONDS = 600


def _recent(key):
    now = time.time()
    _FAILS[key] = [t for t in _FAILS.get(key, []) if now - t < WINDOW_SECONDS]
    return _FAILS[key]


def is_locked(key):
    return len(_recent(key)) >= MAX_FAILS


def register_failure(key):
    _recent(key).append(time.time())


def clear_failures(key):
    _FAILS.pop(key, None)

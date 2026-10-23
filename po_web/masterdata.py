# -*- coding: utf-8 -*-
"""
masterdata.py - the records an order points at: SUPPLIER, PROJECT, SITE.

Every purchase order now stores the IDs of these records (PurchaseOrders.SupplierID / ProjectID / SiteID) next to the
names, so reports and dashboards can follow orders by supplier, project or site reliably. A name typed on an order is
resolved here: the same name written slightly differently (spaces, hamza, ta marbuta, tashkeel) finds the existing
record; a new name CREATES the record - a supplier typed on an order appears on the suppliers page by itself.
All functions accept `tx` so they run inside the same all-or-nothing transaction as the order.
"""
from datetime import datetime

import db
from textnorm import clean_spaces, has_diacritics, norm_key

NO_SITE = "بدون موقع"


def _q(tx):
    return tx or db


def resolve_supplier(name, tx=None, create=True):
    """(SupplierID, official name, created)."""
    name = clean_spaces(name)
    if not name:
        return None, "", False
    key = norm_key(name)
    for r in _q(tx).query("SELECT [SupplierID], [Name] FROM [Suppliers]"):
        if norm_key(r["Name"]) == key:
            return r["SupplierID"], r["Name"], False
    if not create:
        return None, name, False
    sid = _q(tx).execute_insert("INSERT INTO [Suppliers] ([Name], [CreatedAt]) VALUES (?, ?)", (name, datetime.now()))
    return sid, name, True


def resolve_project(name, tx=None, create=True):
    """(ProjectID, official name, created)."""
    name = clean_spaces(name)
    if not name:
        return None, "", False
    key = norm_key(name)
    for r in _q(tx).query("SELECT [ProjectID], [ProjectName], [NameKey], [IsActive] FROM [Projects]"):
        if (r["NameKey"] or norm_key(r["ProjectName"])) == key:
            if not r["IsActive"]:
                _q(tx).execute("UPDATE [Projects] SET [IsActive] = True WHERE [ProjectID] = ?", (r["ProjectID"],))
            return r["ProjectID"], r["ProjectName"], False
    if not create:
        return None, name, False
    pid = _q(tx).execute_insert("INSERT INTO [Projects] ([ProjectName], [NameKey], [IsActive], [CreatedAt]) VALUES (?, ?, True, ?)",
                                (name, key, datetime.now()))
    return pid, name, True


def resolve_site(project_id, name, tx=None, create=True):
    """(SiteID, official name, created) - a site belongs to one project."""
    name = clean_spaces(name)
    if not name or not project_id:
        return None, name, False
    key = norm_key(name)
    for r in _q(tx).query("SELECT [SiteID], [SiteName], [NameKey] FROM [Sites] WHERE [ProjectID] = ?", (project_id,)):
        if (r["NameKey"] or norm_key(r["SiteName"])) == key:
            return r["SiteID"], r["SiteName"], False
    if not create:
        return None, name, False
    sid = _q(tx).execute_insert("INSERT INTO [Sites] ([ProjectID], [SiteName], [NameKey], [IsActive], [CreatedAt]) VALUES (?, ?, ?, True, ?)",
                                (project_id, name, key, datetime.now()))
    return sid, name, True


def best_spelling(variants):
    """{spelling: times used} -> the most used spelling, preferring one without tashkeel."""
    return max(variants.items(), key=lambda kv: (kv[1], not has_diacritics(kv[0]), len(kv[0])))[0]

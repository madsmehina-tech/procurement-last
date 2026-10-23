# -*- coding: utf-8 -*-
"""order_services - split out of the old single app.py (v9) without changing behaviour."""
from datetime import datetime, timedelta

from flask import request

import db

def filtered_orders(columns="*"):
    """Reads q/project/supplier/date_from/date_to from the request's query
    string and returns (rows, filter_context) - shared by the tracking list
    and the analytics dashboard so both filter identically."""
    q = request.args.get("q", "").strip()
    f_project = request.args.get("project", "").strip()
    f_supplier = request.args.get("supplier", "").strip()
    f_site = request.args.get("site", "").strip()
    f_date_from = request.args.get("date_from", "").strip()
    f_date_to = request.args.get("date_to", "").strip()

    base = f"SELECT {columns} FROM [PurchaseOrders]"
    where = []
    params = []
    if q:
        where.append("([PONumber] LIKE ? OR [SupplierName] LIKE ? OR [Project] LIKE ?)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if f_project:
        where.append("[Project] = ?")
        params.append(f_project)
    if f_supplier:
        where.append("[SupplierName] = ?")
        params.append(f_supplier)
    if f_site:
        where.append("[Location] = ?")
        params.append(f_site)
    if f_date_from:
        try:
            where.append("[OrderDate] >= ?")
            params.append(datetime.strptime(f_date_from, "%Y-%m-%d"))
        except ValueError:
            f_date_from = ""
    if f_date_to:
        try:
            where.append("[OrderDate] < ?")
            params.append(datetime.strptime(f_date_to, "%Y-%m-%d") + timedelta(days=1))
        except ValueError:
            f_date_to = ""

    sql = base + ((" WHERE " + " AND ".join(where)) if where else "") + " ORDER BY [CreatedAt] DESC"
    rows = db.query(sql, tuple(params))
    ctx = {"q": q, "f_project": f_project, "f_supplier": f_supplier, "f_site": f_site,
           "f_date_from": f_date_from, "f_date_to": f_date_to}
    return rows, ctx


def filter_choices():
    all_projects = db.query("SELECT DISTINCT [Project] FROM [PurchaseOrders] ORDER BY [Project]")
    all_suppliers = db.query("SELECT DISTINCT [SupplierName] FROM [PurchaseOrders] ORDER BY [SupplierName]")
    return all_projects, all_suppliers


def site_choices():
    return sorted({r["Location"] for r in db.query("SELECT DISTINCT [Location] FROM [PurchaseOrders]") if r.get("Location")})


def supplier_project_choices():
    supplier_names = {r["Name"] for r in db.query("SELECT [Name] FROM [Suppliers]")}
    supplier_names |= {r["SupplierName"] for r in db.query("SELECT DISTINCT [SupplierName] FROM [PurchaseOrders]")}
    suppliers = [{"SupplierName": n} for n in sorted(supplier_names) if n]
    import projects as project_list
    names = project_list.names()
    if not names:                                     # the project list is built by migrate.py; until then use what was typed
        names = sorted({r["Project"] for r in db.query("SELECT DISTINCT [Project] FROM [PurchaseOrders]") if r["Project"]})
    return suppliers, [{"Project": n} for n in names]


def official_supplier(name):
    """The registered spelling of a supplier if the typed name is the same name written slightly differently
    (extra space, hamza, ta marbuta); otherwise the name tidied of extra spaces."""
    from textnorm import clean_spaces, norm_key
    name = clean_spaces(name)
    key = norm_key(name)
    for s in supplier_project_choices()[0]:
        if norm_key(s["SupplierName"]) == key:
            return s["SupplierName"]
    return name


def suggest_supplier(name, threshold=0.84):
    """(registered name, score) closest to what was READ from a quote - used to correct a damaged name."""
    from textnorm import best_match
    return best_match(name, [s["SupplierName"] for s in supplier_project_choices()[0]], threshold)


def locations_list():
    """Distinct site/location names used on past purchase orders, for the
    autocomplete on the location field - independent from the subcontractor
    "multiple sites per contract" concept, which is a separate list."""
    rows = db.query("SELECT DISTINCT [Location] FROM [PurchaseOrders] WHERE [Location] IS NOT NULL")
    return sorted({r["Location"] for r in rows if r["Location"]})


def next_po_number():
    """Suggests the next "PO-<year>-NNN" number for today's year - still
    fully editable by the user, this is only a starting suggestion."""
    year = datetime.now().year
    prefix = f"PO-{year}-"
    rows = db.query(
        "SELECT [PONumber] FROM [PurchaseOrders] WHERE [PONumber] LIKE ?", (f"{prefix}%",)
    )
    best = 0
    for r in rows:
        tail = r["PONumber"][len(prefix):]
        if tail.isdigit():
            best = max(best, int(tail))
    return f"{prefix}{best + 1:03d}"


def receivers_list():
    """Every known (name, phone) pair, for the linked-autofill datalists."""
    return db.query("SELECT [Name], [Phone] FROM [Receivers] ORDER BY [Name]")


def remember_receiver(name, phone):
    name = (name or "").strip()
    phone = (phone or "").strip()
    if not name and not phone:
        return
    now = datetime.now()
    by_name = db.query("SELECT [ReceiverID] FROM [Receivers] WHERE [Name] = ?", (name,)) if name else []
    by_phone = db.query("SELECT [ReceiverID] FROM [Receivers] WHERE [Phone] = ?", (phone,)) if phone else []
    if by_name:
        db.execute("UPDATE [Receivers] SET [Phone] = ?, [UpdatedAt] = ? WHERE [ReceiverID] = ?",
                   (phone, now, by_name[0]["ReceiverID"]))
    elif by_phone:
        db.execute("UPDATE [Receivers] SET [Name] = ?, [UpdatedAt] = ? WHERE [ReceiverID] = ?",
                   (name, now, by_phone[0]["ReceiverID"]))
    else:
        db.execute("INSERT INTO [Receivers] ([Name], [Phone], [UpdatedAt]) VALUES (?, ?, ?)", (name, phone, now))


def hijri_display(dt):
    """Best-effort Hijri date for display only - returns '' if the
    hijri-converter package isn't installed (pip install -r requirements.txt
    picks it up; nothing else depends on it)."""
    try:
        try:
            from hijridate import convert          # current package name
        except ImportError:
            from hijri_converter import convert    # the old name
        h = convert.Gregorian(dt.year, dt.month, dt.day).to_hijri()
        return f"{h.day:02d}/{h.month:02d}/{h.year} هـ"
    except Exception:
        return ""

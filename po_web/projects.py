# -*- coding: utf-8 -*-
"""
projects.py - the single list of project names.

A project is typed by hand on orders and contracts, so «مجمع النرجس» and «مجمع النرجس » (extra space) or
«مدرسه» / «مدرسة» used to become different projects in the dashboard and the archive. Now every project name
is resolved here: the same project (ignoring spaces, hamza, ta marbuta ...) always gets its ONE official spelling.
"""
import db
from textnorm import clean_spaces, norm_key


def all_projects(active_only=False):
    rows = db.query("SELECT [ProjectID], [ProjectName], [NameKey], [IsActive], [CreatedAt] FROM [Projects]")
    rows = [r for r in rows if r["IsActive"] or not active_only]
    return sorted(rows, key=lambda r: norm_key(r["ProjectName"]))


def names(active_only=True):
    return [r["ProjectName"] for r in all_projects(active_only)]


def canonical(name, create=True, tx=None):
    """Returns (official_name, created). An unknown project is created (so nobody is blocked) with the spelling
    the user typed, tidied of extra spaces."""
    import masterdata
    _pid, official, created = masterdata.resolve_project(name, tx=tx, create=create)
    return official, created


def usage():
    """{project name: (orders, contracts)} - how much each project is used."""
    out = {}
    for r in db.query("SELECT [Project] FROM [PurchaseOrders]"):
        out.setdefault(r["Project"], [0, 0])[0] += 1
    for r in db.query("SELECT [Project] FROM [Contracts]"):
        out.setdefault(r["Project"], [0, 0])[1] += 1
    return out


def _retarget(old, new, old_id=None, new_id=None):
    """Orders and contracts follow a renamed / merged project - by NAME and by ID."""
    if old != new:
        db.execute("UPDATE [PurchaseOrders] SET [Project] = ? WHERE [Project] = ?", (new, old))
        db.execute("UPDATE [Contracts] SET [Project] = ? WHERE [Project] = ?", (new, old))
    if old_id and new_id and old_id != new_id:
        db.execute("UPDATE [PurchaseOrders] SET [ProjectID] = ? WHERE [ProjectID] = ?", (new_id, old_id))
        db.execute("UPDATE [Contracts] SET [ProjectID] = ? WHERE [ProjectID] = ?", (new_id, old_id))


def rename(project_id, new_name):
    """Returns an error text, or None on success. Every order and contract is renamed with it."""
    new_name = clean_spaces(new_name)
    if len(new_name) < 2:
        return "اكتب اسم المشروع."
    rows = [r for r in all_projects() if r["ProjectID"] == project_id]
    if not rows:
        return "المشروع غير موجود."
    key = norm_key(new_name)
    if any(r["ProjectID"] != project_id and norm_key(r["ProjectName"]) == key for r in all_projects()):
        return "يوجد مشروع بهذا الاسم. استخدم «دمج» بدل إعادة التسمية."
    old = rows[0]["ProjectName"]
    with db.transaction() as tx:
        tx.execute("UPDATE [Projects] SET [ProjectName] = ?, [NameKey] = ? WHERE [ProjectID] = ?", (new_name, key, project_id))
        for table in ("PurchaseOrders", "Contracts"):                    # by ID, and the rows written before IDs existed by name
            tx.execute("UPDATE [%s] SET [Project] = ? WHERE [ProjectID] = ?" % table, (new_name, project_id))
            tx.execute("UPDATE [%s] SET [Project] = ? WHERE [Project] = ?" % table, (new_name, old))
    return None


def merge(source_id, target_id):
    """Moves every order, contract AND site of the source project to the target, then removes the source record.
    A site that exists under both projects (same name) becomes one: everything that pointed at it follows."""
    if source_id == target_id:
        return "اختر مشروعين مختلفين."
    by_id = {r["ProjectID"]: r for r in all_projects()}
    if source_id not in by_id or target_id not in by_id:
        return "المشروع غير موجود."
    src, dst = by_id[source_id]["ProjectName"], by_id[target_id]["ProjectName"]
    import sites_service
    with db.transaction() as tx:
        sites_service.move_all_sites(source_id, target_id, tx)
        for table in ("PurchaseOrders", "Contracts"):
            tx.execute("UPDATE [%s] SET [Project] = ?, [ProjectID] = ? WHERE [ProjectID] = ?" % table, (dst, target_id, source_id))
            tx.execute("UPDATE [%s] SET [Project] = ?, [ProjectID] = ? WHERE [Project] = ?" % table, (dst, target_id, src))
        tx.execute("DELETE FROM [Projects] WHERE [ProjectID] = ?", (source_id,))
    return None


def set_active(project_id, active):
    db.execute("UPDATE [Projects] SET [IsActive] = ? WHERE [ProjectID] = ?", (bool(active), project_id))

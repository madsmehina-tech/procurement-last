# -*- coding: utf-8 -*-
"""
sites_service.py - the sites of each project: usage, rename, merge, activate.

A site is referenced by purchase orders (PurchaseOrders.SiteID + Location), by contracts (ContractSites.SiteRefID +
SiteName) and, by name, by certificates. Renaming or merging follows ALL of them in one all-or-nothing change.
Archive folders keep the name they were created with (their location is stored on each record), so renaming never
moves or breaks a file.
"""
import db
from textnorm import clean_spaces, norm_key


def all_sites():
    projects = {p["ProjectID"]: p["ProjectName"] for p in db.query("SELECT [ProjectID], [ProjectName] FROM [Projects]")}
    rows = db.query("SELECT * FROM [Sites]")
    orders = db.query("SELECT [SiteID], [TotalAmount] FROM [PurchaseOrders]")
    csites = db.query("SELECT [SiteRefID], [ContractID], [SiteName] FROM [ContractSites]")
    contracts = {c["ContractID"]: c for c in db.query("SELECT [ContractID], [Project], [ProjectID] FROM [Contracts]")}
    certs = db.query("SELECT [ContractID], [SiteName], [SiteID], [ProjectID] FROM [Certificates]")
    out = []
    for s in rows:
        n_orders = [o for o in orders if o.get("SiteID") == s["SiteID"]]
        mine = [c for c in csites if c.get("SiteRefID") == s["SiteID"]]
        names = {norm_key(s["SiteName"])}
        n_certs = [g for g in certs if g.get("SiteID") == s["SiteID"] or (not g.get("SiteID") and norm_key(g.get("SiteName")) in names
                   and ((contracts.get(g.get("ContractID")) or {}).get("ProjectID") or g.get("ProjectID")) == s["ProjectID"])]
        out.append(dict(s, Project=projects.get(s["ProjectID"], "؟"), orders=len(n_orders), amount=sum(o["TotalAmount"] or 0 for o in n_orders),
                        contracts=len({c["ContractID"] for c in mine}), certs=len(n_certs)))
    out.sort(key=lambda r: (norm_key(r["Project"]), norm_key(r["SiteName"])))
    return out


def _retarget(tx, project_id, old_name, new_name, old_id=None, new_id=None):
    """Orders, contract sites and certificates that carry the old site follow the new one."""
    if old_id and new_id and old_id != new_id:
        tx.execute("UPDATE [PurchaseOrders] SET [SiteID] = ? WHERE [SiteID] = ?", (new_id, old_id))
        tx.execute("UPDATE [ContractSites] SET [SiteRefID] = ? WHERE [SiteRefID] = ?", (new_id, old_id))
        tx.execute("UPDATE [Certificates] SET [SiteID] = ? WHERE [SiteID] = ?", (new_id, old_id))
    if old_name != new_name:
        tx.execute("UPDATE [PurchaseOrders] SET [Location] = ? WHERE [SiteID] = ?", (new_name, new_id or old_id))
        tx.execute("UPDATE [ContractSites] SET [SiteName] = ? WHERE [SiteRefID] = ?", (new_name, new_id or old_id))
        for c in tx.query("SELECT [ContractID], [SiteName] FROM [Contracts] WHERE [ProjectID] = ?", (project_id,)):
            if norm_key(c.get("SiteName")) == norm_key(old_name):
                tx.execute("UPDATE [Contracts] SET [SiteName] = ? WHERE [ContractID] = ?", (new_name, c["ContractID"]))
            for g in tx.query("SELECT [CertificateID], [SiteName] FROM [Certificates] WHERE [ContractID] = ?", (c["ContractID"],)):
                if norm_key(g.get("SiteName")) == norm_key(old_name):
                    tx.execute("UPDATE [Certificates] SET [SiteName] = ? WHERE [CertificateID] = ?", (new_name, g["CertificateID"]))
        tx.execute("UPDATE [Certificates] SET [SiteName] = ? WHERE [SiteID] = ?", (new_name, new_id or old_id))   # certificates that carry the site by id


def rename(site_id, new_name):
    """Error text, or None. Everything that carries the site is renamed with it."""
    new_name = clean_spaces(new_name)
    if len(new_name) < 2:
        return "اكتب اسم الموقع."
    rows = db.query("SELECT * FROM [Sites] WHERE [SiteID] = ?", (site_id,))
    if not rows:
        return "الموقع غير موجود."
    s = rows[0]
    key = norm_key(new_name)
    if any(r["SiteID"] != site_id and (r["NameKey"] or norm_key(r["SiteName"])) == key
           for r in db.query("SELECT * FROM [Sites] WHERE [ProjectID] = ?", (s["ProjectID"],))):
        return "يوجد موقع بهذا الاسم في المشروع نفسه. استخدم «دمج» بدل إعادة التسمية."
    with db.transaction() as tx:
        tx.execute("UPDATE [Sites] SET [SiteName] = ?, [NameKey] = ? WHERE [SiteID] = ?", (new_name, key, site_id))
        _retarget(tx, s["ProjectID"], s["SiteName"], new_name, site_id, site_id)
    return None


def merge(source_id, target_id):
    """Error text, or None. Both sites must belong to the same project (use the project merge for different projects)."""
    if source_id == target_id:
        return "اختر موقعين مختلفين."
    by_id = {r["SiteID"]: r for r in db.query("SELECT * FROM [Sites]")}
    if source_id not in by_id or target_id not in by_id:
        return "الموقع غير موجود."
    src, dst = by_id[source_id], by_id[target_id]
    if src["ProjectID"] != dst["ProjectID"]:
        return "الموقعان من مشروعين مختلفين. ادمج المشروعين أولًا."
    with db.transaction() as tx:
        _retarget(tx, src["ProjectID"], src["SiteName"], dst["SiteName"], source_id, target_id)
        tx.execute("DELETE FROM [Sites] WHERE [SiteID] = ?", (source_id,))
    return None


def set_active(site_id, active):
    db.execute("UPDATE [Sites] SET [IsActive] = ? WHERE [SiteID] = ?", (bool(active), site_id))


def move_all_sites(source_project_id, target_project_id, tx):
    """Project merge: the source project's sites move to the target; a site with the same name there is merged into it."""
    target_sites = {(r["NameKey"] or norm_key(r["SiteName"])): r for r in tx.query("SELECT * FROM [Sites] WHERE [ProjectID] = ?", (target_project_id,))}
    for s in tx.query("SELECT * FROM [Sites] WHERE [ProjectID] = ?", (source_project_id,)):
        same = target_sites.get(s["NameKey"] or norm_key(s["SiteName"]))
        if same:
            _retarget(tx, target_project_id, s["SiteName"], same["SiteName"], s["SiteID"], same["SiteID"])
            tx.execute("DELETE FROM [Sites] WHERE [SiteID] = ?", (s["SiteID"],))
        else:
            tx.execute("UPDATE [Sites] SET [ProjectID] = ? WHERE [SiteID] = ?", (target_project_id, s["SiteID"]))

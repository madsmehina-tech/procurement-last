# -*- coding: utf-8 -*-
r"""
m017 - the records behind every purchase order, the site level of the order archive, and two schema gaps.

1. Contracts.Notes (MEMO): the contracts table was created WITHOUT it, so saving a contract with notes failed
   ("Too few parameters"). Suppliers.RepName / RepEmail: the supplier's representative.
2. Sites table (a site belongs to a project) and PurchaseOrders.SupplierID / ProjectID / SiteID.
3. Every existing order is linked: a supplier typed on an order that is not on the suppliers page is CREATED there,
   projects and sites likewise (spellings of the same name are unified to the most used one).
4. Order folders get the site level:  الموردين\<المورد>\<المشروع>\<الموقع>\<رقم الأمر>\...  (files moved safely).
5. Certificate statuses written differently (extra spaces, tashkeel) are unified to the four statuses.
Safe to run again.
"""
import archive
import db
import masterdata
from finance import CERT_STATUSES, norm_status
from migrations._util import add_column
from textnorm import clean_spaces, norm_key

DESCRIPTION = "ربط الأوامر بالمورد والمشروع والموقع ومستوى الموقع في الأرشيف وأعمدة ناقصة"


def schema():
    add_column("Contracts", "Notes", "MEMO")
    add_column("Suppliers", "RepName", "TEXT(100)")
    add_column("Suppliers", "RepEmail", "TEXT(150)")
    for col in ("SupplierID", "ProjectID", "SiteID"):
        add_column("PurchaseOrders", col, "INTEGER")
    if not db.table_exists("Sites"):
        db.execute("CREATE TABLE [Sites] ([SiteID] AUTOINCREMENT PRIMARY KEY, [ProjectID] INTEGER, [SiteName] TEXT(150), "
                   "[NameKey] TEXT(150), [IsActive] YESNO, [CreatedAt] DATETIME)")
        print("- Sites: created")


def link_orders():
    orders = db.query("SELECT [PONumber], [SupplierName], [Project], [Location], [SupplierID], [ProjectID], [SiteID] FROM [PurchaseOrders]")
    # unify spellings first, so the record gets the most used one
    spell = {}
    for o in orders:
        n = clean_spaces(o["SupplierName"])
        if n:
            spell.setdefault(norm_key(n), {}).setdefault(n, 0)
            spell[norm_key(n)][n] += 1
    created = {"supplier": 0, "project": 0, "site": 0}
    for key, variants in spell.items():
        _sid, _name, new = masterdata.resolve_supplier(masterdata.best_spelling(variants))
        created["supplier"] += new
    linked = 0
    for o in orders:
        sid, sname, _n = masterdata.resolve_supplier(o["SupplierName"], create=bool(clean_spaces(o["SupplierName"])))
        pid, pname, new_p = masterdata.resolve_project(o["Project"])
        site_id, site_name, new_s = masterdata.resolve_site(pid, o.get("Location"))
        created["project"] += new_p
        created["site"] += new_s
        if (sid, pid, site_id) != (o.get("SupplierID"), o.get("ProjectID"), o.get("SiteID")) or sname != o["SupplierName"]:
            db.execute("UPDATE [PurchaseOrders] SET [SupplierID] = ?, [ProjectID] = ?, [SiteID] = ?, [SupplierName] = ?, [Project] = ?, [Location] = ? "
                       "WHERE [PONumber] = ?", (sid, pid, site_id, sname or o["SupplierName"], pname or o["Project"],
                                                site_name if site_id else o.get("Location"), o["PONumber"]))
            linked += 1
    print("- orders linked: %d | suppliers created from orders: %d | projects: %d | sites: %d" % (linked, created["supplier"], created["project"], created["site"]))


def site_folders():
    moved = missing = n = 0
    for o in db.query("SELECT [PONumber], [SupplierName], [Project], [Location], [ArchiveFolder] FROM [PurchaseOrders]"):
        want = archive.po_folder_rel(o["SupplierName"], o["Project"], o["PONumber"], o.get("Location"))
        if (o.get("ArchiveFolder") or "") != want:
            m, x = archive.move_po_folder(o["PONumber"], want)
            moved += m
            missing += x
            n += 1
    print("- order folders moved under their site: %d | files moved: %d | missing: %d" % (n, moved, missing))


def statuses():
    n = unknown = 0
    for g in db.query("SELECT [CertificateID], [Status] FROM [Certificates]"):
        s = norm_status(g["Status"])
        if s != g["Status"] and s in CERT_STATUSES:
            db.execute("UPDATE [Certificates] SET [Status] = ? WHERE [CertificateID] = ?", (s, g["CertificateID"]))
            n += 1
        if s not in CERT_STATUSES:
            unknown += 1
    print("- certificate statuses unified: %d | unknown statuses left for review: %d" % (n, unknown))


def main():
    schema()
    link_orders()
    site_folders()
    statuses()


if __name__ == "__main__":
    main()

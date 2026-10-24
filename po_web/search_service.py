"""One search box for the whole system: purchase orders, suppliers, contractors, contracts, certificates, quotation comparisons.

The filtering is done in Python on the normalised text (Arabic letter forms, digits and spaces are ignored: «احمد» finds «أحمد», «١٢٣» finds «123»),
the same way the rest of the system compares names - never with SQL LIKE, whose wildcards differ between Access and other databases.
Each group appears only for a person who may open its pages.
"""
import logging

import auth
import db
from textnorm import norm_key

log = logging.getLogger(__name__)
LIMIT = 8


def _hit(words, *texts):
    blob = norm_key(" ".join(str(t or "") for t in texts))
    return all(w in blob for w in words)


def _rows(sql):
    try:
        return db.query(sql)
    except Exception:                                   # noqa - a missing table / column must not break the whole search
        log.exception("search query failed: %s", sql[:60])
        return []


def search(q, url_for):
    """[{title, items: [{label, note, url}]}] - at most LIMIT items per group."""
    words = [w for w in norm_key(q).split(" ") if w]
    if not words:
        return []
    groups = []

    def group(title, items):
        if items:
            groups.append({"title": title, "items": items[:LIMIT]})

    if auth.can("orders.view"):
        rows = _rows("SELECT [PONumber], [SupplierName], [Project], [TotalAmount], [QuoteNo] FROM [PurchaseOrders] ORDER BY [CreatedAt] DESC")
        group("أوامر الشراء", [{"label": r["PONumber"], "note": "%s · %s · %s" % (r.get("SupplierName") or "", r.get("Project") or "", "{:,.0f}".format(r.get("TotalAmount") or 0)),
                                "url": url_for("order_detail", po_number=r["PONumber"])}
                               for r in rows if _hit(words, r["PONumber"], r.get("SupplierName"), r.get("Project"), r.get("QuoteNo"))])
    if auth.can("suppliers.view"):
        rows = _rows("SELECT [SupplierID], [Name], [CR], [TaxNumber], [Phone] FROM [Suppliers]")
        group("الموردون", [{"label": r["Name"], "note": r.get("Phone") or "", "url": url_for("supplier_detail", supplier_id=r["SupplierID"])}
                           for r in rows if _hit(words, r.get("Name"), r.get("CR"), r.get("TaxNumber"), r.get("Phone"))])
    subs = []
    if auth.can("subcontractors.view") or auth.can("contracts.view") or auth.can("certificates.view"):
        subs = _rows("SELECT [SubcontractorID], [Name] FROM [Subcontractors]")
    if auth.can("subcontractors.view"):
        group("مقاولو الباطن", [{"label": r["Name"], "note": "", "url": url_for("subcontractor_detail", sub_id=r["SubcontractorID"])}
                                for r in subs if _hit(words, r.get("Name"))])
    names = {s["SubcontractorID"]: s["Name"] for s in subs}
    if auth.can("contracts.view"):
        rows = _rows("SELECT [ContractID], [SubcontractorID], [ContractNo], [Project] FROM [Contracts]")
        group("العقود", [{"label": "%s — %s" % (r.get("ContractNo") or ("عقد #%s" % r["ContractID"]), names.get(r.get("SubcontractorID"), "")),
                          "note": r.get("Project") or "", "url": url_for("contract_detail", contract_id=r["ContractID"])}
                         for r in rows if _hit(words, r.get("ContractNo"), r.get("Project"), names.get(r.get("SubcontractorID")))])
    if auth.can("certificates.view"):
        rows = _rows("SELECT [CertificateID], [SubcontractorID], [CertificateNo], [SiteName], [ProjectID] FROM [Certificates]")
        projects = {p["ProjectID"]: p["ProjectName"] for p in _rows("SELECT [ProjectID], [ProjectName] FROM [Projects]")}
        group("المستخلصات", [{"label": "مستخلص رقم %s — %s" % (r.get("CertificateNo"), names.get(r.get("SubcontractorID"), "")),
                              "note": "%s · %s" % (projects.get(r.get("ProjectID"), ""), r.get("SiteName") or ""), "url": url_for("certificate_detail", cert_id=r["CertificateID"])}
                             for r in rows if _hit(words, "مستخلص", r.get("CertificateNo"), names.get(r.get("SubcontractorID")), projects.get(r.get("ProjectID")), r.get("SiteName"))])
    return groups

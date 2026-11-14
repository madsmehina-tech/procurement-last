# -*- coding: utf-8 -*-
"""
insights.py - the charts + filters panel shown at the top of the home page and of every main page (28.0).

One row shape for everything, so one panel, one filter and one export serve all the pages:
    {kind, no, date, project, party, party_kind, amount (riyals), status, href}
Each page names the row sources it shows (PAGES). The panel is drawn in the browser from these rows (static/insights.js + Chart.js, both shipped
with the system - no internet). The SAME filter is applied here in Python for the Excel export, so the file holds exactly what the screen shows.
Nothing here writes to the database.
"""
import io
from datetime import datetime

from flask import url_for

import db
from textnorm import norm_key

KIND_NAME = {"po": "أمر شراء", "cert": "مستخلص", "pr": "أمر صرف", "pay": "دفعة", "cycle": "دورة صرف", "item": "صنف مشترى", "cmp": "مقارنة عروض"}
KINDS_NAME = {"po": "أوامر الشراء", "cert": "المستخلصات", "pr": "أوامر الصرف", "pay": "الدفعات", "cycle": "دورات الصرف", "item": "الأصناف", "cmp": "مقارنات العروض"}


def _f(x):
    try:
        return round(float(x or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _href(endpoint, **kw):
    try:
        return url_for(endpoint, **kw)
    except Exception:                                   # noqa - outside a request (the export, the tests): no link
        return ""


# ------------------------------------------------------------------------------------------------------------- the row sources
def rows_orders():
    out = []
    for o in db.query("SELECT [PONumber], [OrderDate], [SupplierName], [Project], [TotalAmount], [PaymentStatus] FROM [PurchaseOrders]"):
        out.append({"kind": "po", "no": o["PONumber"], "date": o.get("OrderDate"), "project": o.get("Project") or "", "party": o.get("SupplierName") or "", "party_kind": "مورد",
                    "amount": _f(o.get("TotalAmount")), "status": o.get("PaymentStatus") or "معلق", "href": _href("order_detail", po_number=o["PONumber"])})
    return out


def _certs():
    import contracts_service as cs
    _subs, _contracts, certs, pays = cs.load()
    return certs, pays, _subs


def rows_certs(closed_only=False):
    certs, _pays, _subs = _certs()
    out = []
    for g in certs:
        if closed_only and not g.get("ClosedAt"):
            continue
        out.append({"kind": "cert", "no": "مستخلص %s" % g.get("CertificateNo"), "date": g.get("CertificateDate"), "project": g.get("Project") or "",
                    "party": g.get("SubcontractorName") or "", "party_kind": "مقاول", "amount": _f(g.get("Total")), "status": g.get("Status") or "",
                    "href": _href("certificate_detail", cert_id=g["CertificateID"])})
    return out


def rows_payments():
    certs, pays, subs = _certs()
    by = {g["CertificateID"]: g for g in certs}
    out = []
    for p in pays:
        g = by.get(p.get("CertificateID")) or {}
        out.append({"kind": "pay", "no": p.get("BankRef") or ("دفعة %s" % p.get("PaymentID")), "date": p.get("PaidAt"), "project": g.get("Project") or "",
                    "party": (subs.get(p.get("SubcontractorID")) or {}).get("Name", ""), "party_kind": "مقاول", "amount": _f(p.get("Amount")), "status": "تم التحويل",
                    "href": _href("certificate_detail", cert_id=p["CertificateID"]) if p.get("CertificateID") else ""})
    return out


def rows_vouchers():
    out = []
    if not db.table_exists("PaymentVouchers"):
        return out
    for v in db.query("SELECT * FROM [PaymentVouchers]"):
        net = v.get("NetSAR") if v.get("NetSAR") is not None else _f(v.get("Net")) * float(v.get("ExchangeRate") or 1)
        out.append({"kind": "pr", "no": v["VoucherNo"], "date": v.get("VoucherDate"), "project": v.get("ProjectName") or "", "party": v.get("PartyName") or "",
                    "party_kind": v.get("PartyKind") or "", "amount": _f(net), "status": v.get("Status") or "", "href": _href("voucher_view", voucher_id=v["VoucherID"])})
    return out


def rows_cycles():
    import cycles_service as cy
    out = []
    for c in cy.list_cycles():
        out.append({"kind": "cycle", "no": "دورة %s" % c.get("CycleNo"), "date": c.get("CycleDate") or c.get("CreatedAt"), "project": "", "party": c.get("Title") or "", "party_kind": "",
                    "amount": _f(c.get("transferred") or c.get("approved") or c.get("requested")), "status": c.get("stage_name") or "", "href": _href("cycle_detail", cycle_id=c["CycleID"])})
    return out


def rows_items():
    orders = {o["PONumber"]: o for o in db.query("SELECT [PONumber], [OrderDate], [SupplierName], [Project], [PaymentStatus], [ExchangeRate] FROM [PurchaseOrders]")}
    out = []
    for ln in db.query("SELECT [PONumber], [ItemName], [Qty], [UnitPrice] FROM [OrderItems]"):
        o = orders.get(ln["PONumber"])
        if not o or not (ln.get("ItemName") or "").strip():
            continue
        out.append({"kind": "item", "no": ln["PONumber"], "date": o.get("OrderDate"), "project": o.get("Project") or "", "party": (ln["ItemName"] or "").strip(), "party_kind": "صنف",
                    "amount": _f(float(ln.get("Qty") or 0) * float(ln.get("UnitPrice") or 0) * float(o.get("ExchangeRate") or 1)), "status": o.get("SupplierName") or "",
                    "href": _href("order_detail", po_number=ln["PONumber"])})
    return out


def rows_compare():
    out = []
    if not db.table_exists("Comparisons"):
        return out
    for c in db.query("SELECT * FROM [Comparisons]"):
        out.append({"kind": "cmp", "no": c.get("Title") or ("مقارنة %s" % c["CompareID"]), "date": c.get("AwardedAt") or c.get("CreatedAt"), "project": c.get("ProjectName") or "",
                    "party": c.get("Kind") or "", "party_kind": "", "amount": _f(c.get("WinnerTotal")), "status": c.get("Status") or "", "href": _href("compare_view", compare_id=c["CompareID"])})
    return out


# key: (title of the panel, [row sources], label of the «party» filter, which part the 3rd chart ranks)
PAGES = {
    "home": ("نظرة عامة: أوامر الشراء والمستخلصات وأوامر الصرف", (rows_orders, rows_certs, rows_vouchers), "المورد أو المقاول"),
    "orders": ("أوامر الشراء", (rows_orders,), "المورد"),
    "suppliers": ("مشتريات الموردين", (rows_orders,), "المورد"),
    "certificates": ("المستخلصات", (rows_certs,), "المقاول"),
    "subcontractors": ("مستخلصات مقاولي الباطن", (rows_certs,), "المقاول"),
    "payments": ("الدفعات", (rows_payments,), "المقاول"),
    "vouchers": ("أوامر الصرف العامة", (rows_vouchers,), "المستفيد"),
    "cycles": ("دورات الصرف", (rows_cycles,), "عنوان الدورة"),
    "items": ("الأصناف المشتراة", (rows_items,), "الصنف"),
    "archive": ("المعاملات المقفلة في الأرشيف", (lambda: rows_certs(closed_only=True),), "المقاول"),
    "compare": ("مقارنات عروض الأسعار", (rows_compare,), "النوع"),
}
PERMS = {"home": "orders.view", "orders": "orders.view", "suppliers": "suppliers.view", "certificates": "certificates.view", "subcontractors": "subcontractors.view",
         "payments": "payments.view", "vouchers": "vouchers.view", "cycles": "cycles.view", "items": "orders.view", "archive": "certificates.view", "compare": "compare.view"}


def rows_for(page, can=None):
    """The rows of a page, as plain values the browser can read (dates as YYYY-MM-DD). On the home page a kind the person may not see is left out."""
    title, sources, _party = PAGES[page]
    out = []
    for src in sources:
        if page == "home" and can is not None:
            need = {rows_orders: "orders.view", rows_certs: "certificates.view", rows_vouchers: "vouchers.view"}.get(src)
            if need and not can(need):
                continue
        try:
            out += src()
        except Exception:                               # noqa - a table of a part that is not set up yet must not break every page
            import logging
            logging.getLogger("po.error").exception("insights source failed: %s", getattr(src, "__name__", "?"))
    for r in out:
        d = r.get("date")
        r["date"] = d.strftime("%Y-%m-%d") if isinstance(d, datetime) else (str(d)[:10] if d else "")
    out.sort(key=lambda r: r["date"], reverse=True)
    return out


def meta(page):
    title, _sources, party = PAGES[page]
    return {"page": page, "title": title, "party_label": party, "kinds": KINDS_NAME, "kind": KIND_NAME}


# ------------------------------------------------------------------------------------------------------------- the filter (the same rules as static/insights.js)
def apply(rows, args):
    """args: kind, from (YYYY-MM), to (YYYY-MM), project, party, status, q  - every one optional."""
    kind, d_from, d_to = (args.get("kind") or "").strip(), (args.get("from") or "").strip()[:7], (args.get("to") or "").strip()[:7]
    project, party, status = (args.get("project") or "").strip(), (args.get("party") or "").strip(), (args.get("status") or "").strip()
    words = [w for w in norm_key(args.get("q") or "").split(" ") if w]
    out = []
    for r in rows:
        month = (r.get("date") or "")[:7]
        if kind and r["kind"] != kind:
            continue
        if d_from and (not month or month < d_from):
            continue
        if d_to and (not month or month > d_to):
            continue
        if project and r["project"] != project:
            continue
        if party and r["party"] != party:
            continue
        if status and r["status"] != status:
            continue
        if words:
            hay = norm_key(" ".join(str(r.get(k) or "") for k in ("no", "party", "project", "status")))
            if not all(w in hay for w in words):
                continue
        out.append(r)
    return out


def summary(rows):
    total = round(sum(r["amount"] for r in rows), 2)
    return {"count": len(rows), "total": total, "average": round(total / len(rows), 2) if rows else 0.0,
            "parties": len({r["party"] for r in rows if r["party"]}), "projects": len({r["project"] for r in rows if r["project"]})}


def to_xlsx(page, rows, args=None):
    """An Excel file of the filtered rows, laid out as a plain table (one header row, one row per record, real dates and numbers) so that
    Power BI / Excel read it directly. A second sheet says which filters were applied."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    import xlsx_safe
    m = meta(page)
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.sheet_view.rightToLeft = True
    head = ["النوع", "الرقم", "التاريخ", "الشهر", "المشروع", m["party_label"], "صفة الطرف", "المبلغ بالريال", "الحالة"]
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="29595E")
    for r in rows:
        try:
            when = datetime.strptime(r["date"], "%Y-%m-%d") if r.get("date") else None
        except ValueError:
            when = None
        ws.append([KIND_NAME.get(r["kind"], r["kind"]), r["no"], when, (r.get("date") or "")[:7], r["project"], r["party"],
                   r.get("party_kind") or "", r["amount"], r["status"]])
    for i, w in enumerate((14, 22, 13, 10, 34, 34, 12, 18, 16), 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for row in ws.iter_rows(min_row=2, min_col=3, max_col=3):
        row[0].number_format = "yyyy-mm-dd"
    for row in ws.iter_rows(min_row=2, min_col=8, max_col=8):
        row[0].number_format = "#,##0.00"
    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = "A1:I%d" % (len(rows) + 1)
    info = wb.create_sheet("Filters")
    info.sheet_view.rightToLeft = True
    s = summary(rows)
    info.append(["اللوحة", m["title"]])
    info.append(["تاريخ التصدير", datetime.now().strftime("%Y-%m-%d %H:%M")])
    for label, key in (("النوع", "kind"), ("من شهر", "from"), ("إلى شهر", "to"), ("المشروع", "project"), (m["party_label"], "party"), ("الحالة", "status"), ("بحث", "q")):
        val = ((args or {}).get(key) or "").strip()
        info.append([label, KINDS_NAME.get(val, val) if key == "kind" else val or "الكل"])
    info.append(["عدد السجلات", s["count"]])
    info.append(["إجمالي المبالغ بالريال", s["total"]])
    info.column_dimensions["A"].width, info.column_dimensions["B"].width = 26, 44
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

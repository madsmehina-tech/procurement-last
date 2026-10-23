# -*- coding: utf-8 -*-
"""
po_files.py - what the system itself writes to an order's archive folder.

    الموردين / <المورد> / <المشروع> / <رقم الأمر> /
        أمر الشراء /      PO-xxx.pdf  (the order as printed)   +   PO-xxx.json  (a copy of its data)
        المرفقات /        quote, receipt note, invoice, payment voucher, extra attachments  (uploaded by people)
        النسخ السابقة /   PO-xxx_v1.pdf + PO-xxx_v1.json ...  (every version replaced by an edit)

Before this module the order itself lived ONLY in the database - the folder held just the quote, which is why a
supplier's folder showed no purchase order. Saving is never all-or-nothing about the PDF: the data copy is always
written; if no browser is available for the PDF the order and its data are still safe and the page says so.
"""
import base64
import json
import os
import tempfile
from datetime import datetime

from flask import render_template

import archive
import arabic_numbers
import config
import db
import order_math
import pdfgen

SNAPSHOT_FIELDS = ("PONumber", "OrderDate", "HijriDate", "SupplierName", "QuoteNo", "Project", "Location", "Receiver",
                   "ReceiverPhone", "TotalAmount", "Discount", "ExtraNotes", "HideWarehouseNote")


def print_context(order, items, watermark=None):
    """Everything order_print.html needs. `order` / `items` may come from the database, from an unsaved form
    (preview) or from an old saved version - the form on paper is the same for all three."""
    items = order_math.with_line_totals(items)
    t = order_math.order_totals([(it["Qty"], it["UnitPrice"]) for it in items], order.get("Discount") or 0)
    subtotal, vat, total_with_vat, discount, grand = t["subtotal"], t["vat"], t["total_with_vat"], t["discount"], t["grand"]
    extra_notes = [l.strip() for l in (order.get("ExtraNotes") or "").splitlines() if l.strip()]
    return dict(order=order, items=items, subtotal=subtotal, vat=vat, total_with_vat=total_with_vat,
                discount=discount, grand=grand, amount_words=arabic_numbers.amount_to_words(grand),
                extra_notes=extra_notes, watermark=watermark)


def load_order(po_number):
    rows = db.query("SELECT * FROM [PurchaseOrders] WHERE [PONumber] = ?", (po_number,))
    if not rows:
        return None, None
    items = db.query("SELECT [LineNo], [ItemName], [Unit], [Qty], [UnitPrice] FROM [OrderItems] "
                     "WHERE [PONumber] = ? ORDER BY [LineNo]", (po_number,))
    return rows[0], items


def revision_count(po_number):
    return len(db.query("SELECT [RevID] FROM [OrderRevisions] WHERE [PONumber] = ?", (po_number,)))


def _logo_data_uri():
    path = os.path.join(config.BASE_DIR, "static", "company_logo.png")
    try:
        with open(path, "rb") as f:
            return "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")
    except OSError:
        return ""


def render_html(order, items, watermark=None):
    return render_template("order_print.html", pdf_mode=True, logo_src=_logo_data_uri(), **print_context(order, items, watermark))


def make_pdf(order, items, watermark=None):
    """The order as PDF bytes (raises pdfgen.PdfError)."""
    html = render_html(order, items, watermark)
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        pdfgen.html_to_pdf(html, path)
        with open(path, "rb") as f:
            return f.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def data_bytes(order, items, version):
    """A copy of the order's DATA as JSON - readable by people and by any future system."""
    ctx = print_context(order, items)
    payload = {
        "format": "po-data/1", "version": version, "saved_at": datetime.now().isoformat(timespec="seconds"),
        "order": {k: order.get(k) for k in SNAPSHOT_FIELDS},
        "items": [{"line": i + 1, "name": it["ItemName"], "unit": it.get("Unit"), "qty": it["Qty"], "unit_price": it["UnitPrice"],
                   "total": round((it["Qty"] or 0) * (it["UnitPrice"] or 0), 2)} for i, it in enumerate(items)],
        "totals": {"subtotal": ctx["subtotal"], "vat_15": ctx["vat"], "total_with_vat": ctx["total_with_vat"],
                   "discount": ctx["discount"], "grand_total": ctx["grand"]},
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, default=lambda o: o.isoformat() if hasattr(o, "isoformat") else str(o)).encode("utf-8")


def _file_stem(po_number):
    return archive.safe_name(po_number, maxlen=30)


def _replace_or_store(po_number, folder, doc_type, content, name, user):
    """The current PDF / data copy of an order is REPLACED in place (one current file, not a growing pile)."""
    current = [d for d in archive.list_documents("PO", po_number) if d["DocType"] == doc_type and d.get("IsCurrent")]
    if current:
        try:
            path = archive.abs_path(current[0]["RelPath"])
            if os.path.isfile(path):
                with open(path, "wb") as f:
                    f.write(content)
                db.execute("UPDATE [Documents] SET [UploadedAt] = ?, [UploadedBy] = ? WHERE [DocID] = ?",
                           (datetime.now(), user or "", current[0]["DocID"]))
                return current[0]
        except (OSError, ValueError):
            pass
    return archive.store_document("PO", po_number, archive.po_doc_folder_rel(folder, doc_type), doc_type, content, name,
                                  user=user, exact_name=name, system=True)


def save_current(po_number, user=""):
    """Writes the order's PDF and data copy to its archive folder. Returns {"pdf": doc|None, "data": doc|None, "error": str}.
    Never raises for a missing browser or a file problem - the caller reports the error text."""
    out = {"pdf": None, "data": None, "error": ""}
    order, items = load_order(po_number)
    if not order:
        out["error"] = "أمر الشراء غير موجود"
        return out
    try:
        folder = archive.ensure_po_folder(po_number)
        stem = _file_stem(po_number)
        out["data"] = _replace_or_store(po_number, folder, archive.PO_DATA, data_bytes(order, items, revision_count(po_number) + 1),
                                        stem + ".json", user)
        out["pdf"] = _replace_or_store(po_number, folder, archive.PO_PDF, make_pdf(order, items), stem + ".pdf", user)
    except pdfgen.PdfError as e:
        out["error"] = str(e)
    except (OSError, ValueError) as e:
        out["error"] = "تعذّر الكتابة في الأرشيف: %s" % e
    return out


def archive_snapshot(po_number, rev_no, order, items, user=""):
    """BEFORE an edit is saved: the version about to be replaced is written to النسخ السابقة. Returns its Documents row.
    If this fails the edit is not made at all - there is never an edit without a safety copy."""
    folder = archive.ensure_po_folder(po_number)
    name = "%s_v%d.json" % (_file_stem(po_number), rev_no)
    return archive.store_document("PO", po_number, archive.po_doc_folder_rel(folder, archive.PO_OLD_DATA), archive.PO_OLD_DATA,
                                  data_bytes(order, items, rev_no), name, user=user, exact_name=name, system=True)


def archive_previous_pdf(po_number, rev_no, order, items, user=""):
    """AFTER the edit is saved, and BEFORE the new PDF replaces the current one: the old version's PDF goes to
    النسخ السابقة. The current PDF (if any) IS the old version; otherwise it is rendered from the saved snapshot."""
    folder = archive.ensure_po_folder(po_number)
    name = "%s_v%d.pdf" % (_file_stem(po_number), rev_no)
    content = None
    current = [d for d in archive.list_documents("PO", po_number) if d["DocType"] == archive.PO_PDF and d.get("IsCurrent")]
    if current:
        try:
            with open(archive.abs_path(current[0]["RelPath"]), "rb") as f:
                content = f.read()
        except (OSError, ValueError):
            content = None
    if content is None:
        content = make_pdf(order, items, "نسخة سابقة رقم %d" % rev_no)       # may raise pdfgen.PdfError
    return archive.store_document("PO", po_number, archive.po_doc_folder_rel(folder, archive.PO_OLD_PDF), archive.PO_OLD_PDF,
                                  content, name, user=user, exact_name=name, system=True)


def read_snapshot(rev):
    """The saved old version of an order: from its archived JSON file, or from the older in-database copy."""
    if rev.get("DataDocID"):
        doc = archive.get_document(rev["DataDocID"])
        if doc:
            try:
                with open(archive.abs_path(doc["RelPath"]), "rb") as f:
                    return json.loads(f.read().decode("utf-8"))
            except (OSError, ValueError):
                pass
    if rev.get("Snapshot"):
        return json.loads(rev["Snapshot"])
    return None


def generated_docs(po_number):
    """{pdf, data, old_pdf: {rev_no: doc}, old_data} for the order page."""
    docs = archive.list_documents("PO", po_number)
    cur = lambda t: next((d for d in docs if d["DocType"] == t and d.get("IsCurrent")), None)
    return {"pdf": cur(archive.PO_PDF), "data": cur(archive.PO_DATA)}

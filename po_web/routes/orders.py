# -*- coding: utf-8 -*-
"""Purchasing: dashboard, order tracking, order creation (with quote reading), status updates, printing."""
import logging
import os
from datetime import datetime, timedelta

from flask import abort, flash, redirect, render_template, request, send_file, url_for

import archive
import auth
import money_guards as mg
import order_math
import db
import items_service
import masterdata
import pdfgen
import po_files
import projects as project_list
from order_services import (filter_choices, filtered_orders, hijri_display, locations_list, next_po_number, site_choices,
                            official_supplier, receivers_list, remember_receiver, suggest_supplier, supplier_project_choices)
from textnorm import clean_spaces
from routes import route
from routes.documents import back, save_entity_document


@route("/")
@auth.login_required
def dashboard():
    if not auth.can("orders.view"):
        return render_template("welcome.html")
    orders_count = db.scalar("SELECT COUNT(*) FROM [PurchaseOrders]", default=0)
    total_amount = db.scalar("SELECT SUM([TotalAmount]) FROM [PurchaseOrders]", default=0) or 0
    due_soon = db.scalar(
        "SELECT COUNT(*) FROM [PurchaseOrders] WHERE [InvoiceDueDate] IS NOT NULL "
        "AND [InvoiceDueDate] <= DATEADD('d', 7, Now()) AND [PaymentStatus] <> 'تم السداد'",
        default=0,
    )
    partial_receipt = db.scalar(
        "SELECT COUNT(*) FROM [PurchaseOrders] WHERE [ReceiptStatus] = 'استلام جزئي'",
        default=0,
    )
    recent = db.query(
        "SELECT TOP 8 [PONumber], [SupplierName], [Project], [TotalAmount], "
        "[ReceiptStatus], [InvoiceStatus], [PaymentStatus] "
        "FROM [PurchaseOrders] ORDER BY [CreatedAt] DESC"
    )
    return render_template(
        "dashboard.html",
        orders_count=orders_count,
        total_amount=total_amount,
        due_soon=due_soon,
        partial_receipt=partial_receipt,
        recent=recent,
    )


@route("/orders")
@auth.login_required
def orders_list():
    rows, ctx = filtered_orders(
        "[PONumber], [OrderDate], [SupplierName], [Project], [TotalAmount], "
        "[ReceiptStatus], [ReceiptDate], [ReceiptNote], [InvoiceStatus], [InvoiceDate], [InvoiceNote], "
        "[PaymentStatus], [PaymentDate], [PaymentNote], [SignedFile]"
    )
    orders_count = len(rows)
    total_amount = 0.0
    receipt_pending = invoice_pending = payment_pending = 0
    for r in rows:
        receipt_done = r["ReceiptStatus"] in ("استلام جزئي", "مكتمل")
        invoice_done = r["InvoiceStatus"] == "تم الإصدار"
        payment_done = r["PaymentStatus"] == "تم السداد"
        if payment_done:
            r["Overall"] = "مكتمل"
        elif invoice_done:
            r["Overall"] = "بانتظار السداد"
        elif receipt_done:
            r["Overall"] = "بانتظار الفاتورة"
        else:
            r["Overall"] = "بانتظار الاستلام"
        r["ReceiptDone"] = receipt_done
        r["InvoiceDone"] = invoice_done
        r["PaymentDone"] = payment_done
        total_amount += r["TotalAmount"] or 0
        if not receipt_done:
            receipt_pending += 1
        if not invoice_done:
            invoice_pending += 1
        if not payment_done:
            payment_pending += 1

    all_projects, all_suppliers = filter_choices()
    return render_template(
        "orders_list.html", rows=rows, orders_count=orders_count, total_amount=total_amount,
        receipt_pending=receipt_pending, invoice_pending=invoice_pending, payment_pending=payment_pending,
        all_projects=all_projects, all_suppliers=all_suppliers, all_sites=site_choices(), **ctx,
    )


@route("/orders/<po_number>")
@auth.login_required
def order_detail(po_number):
    order = db.query("SELECT * FROM [PurchaseOrders] WHERE [PONumber] = ?", (po_number,))
    if not order:
        flash("لم يتم العثور على أمر الشراء.")
        return redirect(url_for("orders_list"))
    items = db.query(
        "SELECT [LineNo], [ItemName], [Unit], [Qty], [UnitPrice], [QtyReceived] "
        "FROM [OrderItems] WHERE [PONumber] = ? ORDER BY [LineNo]",
        (po_number,),
    )
    o = order[0]
    folder = o.get("ArchiveFolder") or archive.po_folder_rel(o.get("SupplierName"), o.get("Project"), po_number)
    revisions = sorted(db.query("SELECT [RevNo], [ChangedAt], [ChangedBy], [Summary], [PdfDocID], [DataDocID] FROM [OrderRevisions] "
                                "WHERE [PONumber] = ?", (po_number,)), key=lambda r: r["RevNo"], reverse=True)
    return render_template(
        "order_detail.html", order=o, items=order_math.with_line_totals(items), revisions=revisions, version=len(revisions) + 1,
        gen=po_files.generated_docs(po_number), pdf_ready=pdfgen.available(),
        docs=archive.panel("PO", po_number, archive.PO_DOC_TYPES), folder=folder,
        folder_exists=bool(o.get("ArchiveFolder")), network=archive.network_path(folder),
    )


STATUS_OPTIONS = {
    "receipt": (["لم يبدأ", "استلام جزئي", "مكتمل"], "ReceiptStatus"),
    "invoice": (["معلق", "تم الإصدار"], "InvoiceStatus"),
    "payment": (["معلق", "تم السداد"], "PaymentStatus"),
}


@route("/orders/<po_number>/status", methods=["POST"])
@auth.login_required
def update_status(po_number):
    kind = request.form.get("kind", "")
    value = request.form.get("value", "")
    note = request.form.get("note", "").strip()
    if kind not in STATUS_OPTIONS or value not in STATUS_OPTIONS[kind][0]:
        flash("طلب غير صالح.")
        return redirect(url_for("order_detail", po_number=po_number))

    column = STATUS_OPTIONS[kind][1]
    now = datetime.now()
    if kind == "receipt":
        db.execute(
            "UPDATE [PurchaseOrders] SET [ReceiptStatus] = ?, [ReceiptDate] = ?, [ReceiptNote] = ?, "
            "[ModifiedAt] = ? WHERE [PONumber] = ?",
            (value, now, note, now, po_number),
        )
    elif kind == "invoice" and value == "تم الإصدار":
        due = now + timedelta(days=30)
        db.execute(
            "UPDATE [PurchaseOrders] SET [InvoiceStatus] = ?, [InvoiceDate] = ?, [InvoiceNote] = ?, "
            "[InvoiceDueDate] = ?, [ModifiedAt] = ? WHERE [PONumber] = ?",
            (value, now, note, due, now, po_number),
        )
    elif kind == "payment" and value == "تم السداد":
        db.execute(
            "UPDATE [PurchaseOrders] SET [PaymentStatus] = ?, [PaymentDate] = ?, [PaymentNote] = ?, "
            "[ModifiedAt] = ? WHERE [PONumber] = ?",
            (value, now, note, now, po_number),
        )
    else:
        db.execute(
            f"UPDATE [PurchaseOrders] SET [{column}] = ?, [ModifiedAt] = ? WHERE [PONumber] = ?",
            (value, now, po_number),
        )
    flash("تم تحديث الحالة.")
    return redirect(url_for("order_detail", po_number=po_number))


@route("/orders/<po_number>/signed-upload", methods=["POST"])
@auth.login_required
def order_signed_upload(po_number):
    """Shortcut used by the tracking list: uploads the signed copy of the order into its archive folder."""
    if save_entity_document("PO", po_number, "أمر الشراء", request.files.get("signed_file")):
        flash("تم رفع النسخة المعتمدة وحفظها في أرشيف الأمر.")
    return back("orders_list")


def _legacy_file(po_number, column):
    rows = db.query("SELECT [%s] AS p FROM [PurchaseOrders] WHERE [PONumber] = ?" % column, (po_number,))
    if not rows or not rows[0]["p"]:
        abort(404)
    return archive.send(rows[0]["p"])


@route("/orders/<po_number>/signed-file")
@auth.login_required
def order_signed_file(po_number):
    return _legacy_file(po_number, "SignedFile")


@route("/orders/<po_number>/quote-file")
@auth.login_required
def order_quote_file(po_number):
    return _legacy_file(po_number, "QuoteFile")


@route("/orders/new/quote-copy/<path:filename>")
@auth.login_required
def order_quote_copy(filename):
    """The Excel copy of a quote that could not be read automatically (kept in the pending folder)."""
    p = archive.pending_path(filename)
    if not p:
        abort(404)
    return send_file(p, as_attachment=True, download_name=os.path.basename(p))


# --------------------------------------------------------------------------- printing, preview, old versions
@route("/orders/<po_number>/print")
@auth.login_required
def order_print(po_number):
    order, items = po_files.load_order(po_number)
    if not order:
        flash("لم يتم العثور على أمر الشراء.")
        return redirect(url_for("orders_list"))
    return render_template("order_print.html", **po_files.print_context(order, items))


@route("/orders/new/preview", methods=["POST"])
@auth.login_required
def order_preview():
    """Shows the printed form for what is typed in the form RIGHT NOW. Nothing is saved or created."""
    form = _form_to_dict()
    v, line_items, _errors = _read_order(form, editing=form.get("po_number"), create_project=False)
    order = {"PONumber": v["po_number"] or "(بلا رقم)", "OrderDate": v["order_date"], "HijriDate": hijri_display(v["order_date"]),
             "SupplierName": v["supplier"], "QuoteNo": v["quote_no"], "Project": v["project"], "Location": v["location"],
             "Receiver": v["receiver"], "ReceiverPhone": v["phone"], "Discount": v["discount"],
             "ExtraNotes": v["extra_notes"], "HideWarehouseNote": v["hide_warehouse_note"]}
    items = [{"LineNo": i, "ItemName": nm, "Unit": un, "Qty": q, "UnitPrice": p} for i, (nm, un, q, p) in enumerate(line_items, 1)]
    return render_template("order_print.html", **po_files.print_context(order, items, "معاينة — لم يُحفظ بعد"))


@route("/orders/<po_number>/revisions/<int:rev_no>/print")
@auth.login_required
def order_revision_print(po_number, rev_no):
    rows = db.query("SELECT [RevNo], [Snapshot], [DataDocID] FROM [OrderRevisions] WHERE [PONumber] = ?", (po_number,))
    rev = next((r for r in rows if r["RevNo"] == rev_no), None)
    snap = po_files.read_snapshot(rev) if rev else None
    if not snap:
        flash("النسخة المطلوبة غير موجودة.")
        return redirect(url_for("order_detail", po_number=po_number))
    order = dict(snap["order"])
    if order.get("OrderDate"):
        order["OrderDate"] = datetime.fromisoformat(order["OrderDate"])
    items = [{"LineNo": it.get("LineNo", i + 1), "ItemName": it.get("ItemName", it.get("name")), "Unit": it.get("Unit", it.get("unit")),
              "Qty": it.get("Qty", it.get("qty")), "UnitPrice": it.get("UnitPrice", it.get("unit_price"))} for i, it in enumerate(snap["items"])]
    return render_template("order_print.html", **po_files.print_context(order, items, "نسخة سابقة رقم %d — استُبدلت بتعديل لاحق" % rev_no))


@route("/orders/<po_number>/pdf", methods=["POST"])
@auth.login_required
def order_pdf(po_number):
    """(Re)writes the order's PDF and data copy to its archive folder - for orders saved before this existed, or when
    the first attempt could not find a browser."""
    res = po_files.save_current(po_number, (auth.current_user() or {}).get("Username", ""))
    if res["pdf"]:
        flash("تم إنشاء ملف PDF وحفظه في أرشيف الأمر (أمر الشراء ← %s)." % res["pdf"]["StoredName"])
    else:
        flash("لم يُنشأ ملف PDF: %s. نُسخت بيانات الأمر في الأرشيف." % (res["error"] or "سبب غير معروف"))
    return redirect(url_for("order_detail", po_number=po_number))


# --------------------------------------------------------------------------- create / revise an order
ORDER_FIELDS = [("SupplierName", "المورد"), ("Project", "المشروع"), ("Location", "الموقع"), ("QuoteNo", "رقم العرض"),
                ("Receiver", "المستلم"), ("ReceiverPhone", "الجوال"), ("ExtraNotes", "الملاحظات")]
FIELD_LABELS = {"SupplierName": "اسم المورد", "Project": "اسم المشروع", "Location": "الموقع", "QuoteNo": "رقم عرض السعر",
                "Receiver": "اسم المستلم", "ReceiverPhone": "جوال المستلم", "ItemName": "اسم الصنف", "Unit": "الوحدة"}


def _form_to_dict(raw=None):
    """request.form as a plain dict where the item columns stay LISTS (a MultiDict would hand the template only
    the first value, so after a validation error the items table came back as single letters)."""
    raw = (raw if raw is not None else request.form).to_dict(flat=False)
    form = {k: (v[0] if v else "") for k, v in raw.items()}
    for key in ("item_name", "item_unit", "item_qty", "item_price"):
        form[key] = raw.get(key, [])
    return form


def _parse_date(text):
    try:
        return datetime.strptime(text, "%Y-%m-%d") if text else datetime.now()
    except ValueError:
        return datetime.now()


def _limits():
    """The real maximum length of each text field, read from the database itself (255 when it cannot be read) - the
    form warns WHILE typing instead of failing at the end."""
    po, it = db.column_limits("PurchaseOrders"), db.column_limits("OrderItems")
    return {"item": it.get("ItemName", 255), "unit": it.get("Unit", 255), "supplier": po.get("SupplierName", 255),
            "project": po.get("Project", 255), "location": po.get("Location", 255), "receiver": po.get("Receiver", 255),
            "phone": po.get("ReceiverPhone", 255), "quote_no": po.get("QuoteNo", 255)}


def _new_context(form, **extra):
    suppliers, projects = supplier_project_choices()
    catalog, units = items_service.catalog()
    ctx = dict(suppliers=suppliers, projects=projects, form=form, next_po=form.get("po_number") or next_po_number(),
               hijri_today=hijri_display(_parse_date(form.get("order_date"))),
               today_iso=datetime.now().strftime("%Y-%m-%d"), receivers=receivers_list(), locations=locations_list(),
               recent_pos=[r["PONumber"] for r in sorted(db.query("SELECT [PONumber], [CreatedAt] FROM [PurchaseOrders]"),
                                                         key=lambda r: r["CreatedAt"] or datetime.min, reverse=True)[:30]],
               catalog=catalog, units=units, limits=_limits(), edit_po=None, warnings=[])
    ctx.update(extra)
    return ctx


def _db_failure(e):
    """A failed save: logged in full, shown to the person in one plain sentence (with the database's own words)."""
    logging.getLogger("po.error").exception("order save failed")
    return "تعذّر الحفظ ولم يتغير أي شيء في القاعدة. السبب: %s" % " ".join(str(e).split())[:230]


def _read_order(form, editing=None, create_project=True):
    """Reads and checks the order form. Returns (values, line_items, errors)."""
    errors = []
    po_number = (editing or form.get("po_number", "")).strip()
    v = {
        "po_number": po_number, "supplier": official_supplier(form.get("supplier", "")),
        "project": clean_spaces(form.get("project", "")), "location": clean_spaces(form.get("location", "")),
        "quote_no": form.get("quote_no", "").strip(), "receiver": clean_spaces(form.get("receiver", "")),
        "phone": form.get("phone", "").strip(), "order_date": _parse_date(form.get("order_date", "").strip()),
        "extra_notes": form.get("extra_notes", "").strip(), "hide_warehouse_note": bool(form.get("hide_warehouse_note")),
    }
    if not po_number:
        errors.append("رقم أمر الشراء مطلوب.")
    elif not editing and db.query("SELECT [PONumber] FROM [PurchaseOrders] WHERE [PONumber] = ?", (po_number,)):
        errors.append("رقم أمر الشراء هذا مستخدم بالفعل - اختر رقمًا آخر.")
    if not v["supplier"]:
        errors.append("اسم المورد مطلوب.")
    if not v["project"]:
        errors.append("اسم المشروع مطلوب.")
    else:                                              # the project's ONE official spelling (never creates one here)
        v["project"] = project_list.canonical(v["project"], create=False)[0]
    try:
        v["discount"] = mg.parse_money(form.get("discount"), label="قيمة الخصم")
    except mg.MoneyError as e:
        v["discount"] = 0.0
        errors.append(str(e))
    line_items, subtotal = [], 0.0
    for nm, un, q, p in zip(form.get("item_name", []), form.get("item_unit", []), form.get("item_qty", []), form.get("item_price", [])):
        nm = clean_spaces(nm)
        if not nm:
            continue
        try:
            qf = mg.parse_money(q, label="الكمية", decimals=4)
            pf = mg.parse_money(p, label="السعر", decimals=4)
        except mg.MoneyError as e:
            errors.append("الصنف «%s»: %s" % (nm, e))
            continue
        line_items.append((nm, clean_spaces(un), qf, pf))
    totals = order_math.order_totals([(q_, p_) for _n, _u, q_, p_ in line_items], v["discount"])      # the SAME arithmetic the printed order uses
    v["subtotal"] = totals["subtotal"]
    v["total"] = totals["grand"]
    if v["total"] < 0:
        errors.append("الخصم أكبر من إجمالي الأمر؛ راجع قيمة الخصم.")
    elif v["total"] > mg.MAX_MONEY:
        errors.append("إجمالي الأمر كبير جدًا (أكثر من %s)؛ راجع الكميات والأسعار." % "{:,.0f}".format(mg.MAX_MONEY))
    if not line_items:
        errors.append("أضف صنفًا واحدًا على الأقل.")
    # text longer than its column would make the database refuse the whole save: say so BEFORE, in plain words
    for col, limit, n in db.too_long("PurchaseOrders", {"SupplierName": v["supplier"], "Project": v["project"], "Location": v["location"],
                                                       "QuoteNo": v["quote_no"], "Receiver": v["receiver"], "ReceiverPhone": v["phone"]}):
        errors.append("%s أطول من المسموح (%d حرفًا، المكتوب %d). اختصره." % (FIELD_LABELS[col], limit, n))
    for i, (nm, un, _q, _p) in enumerate(line_items, 1):
        for col, limit, n in db.too_long("OrderItems", {"ItemName": nm, "Unit": un}):
            errors.append("%s في السطر %d أطول من المسموح (%d حرفًا، المكتوب %d). اختصره." % (FIELD_LABELS[col], i, limit, n))
    return v, line_items, errors


def _save_items(po_number, line_items, tx):
    for i, (nm, un, qf, pf) in enumerate(line_items, start=1):
        tx.execute("INSERT INTO [OrderItems] ([PONumber], [LineNo], [ItemName], [Unit], [Qty], [UnitPrice], [QtyReceived]) "
                   "VALUES (?, ?, ?, ?, ?, ?, 0)", (po_number, i, nm, un, qf, pf))


def _announce(new_supplier, new_project, new_site, supplier_id):
    """Says what was created on the way, and gives a new supplier its archive folder."""
    if new_supplier:
        archive.ensure_supplier_folder(supplier_id)
        flash("أُضيف المورد «%s» إلى صفحة الموردين تلقائيًا (أكمل بياناته وأوراقه من صفحته)." % new_supplier)
    if new_project:
        flash("أُضيف مشروع جديد إلى القائمة: %s" % new_project)
    if new_site:
        flash("أُضيف موقع جديد للمشروع: %s" % new_site)


def _files_message(res, what):
    if res["pdf"]:
        return "%s وحُفظ أمر الشراء (PDF) ونسخة بياناته في أرشيف المورد." % what
    return "%s. لم يُنشأ ملف PDF (%s)، لكن نُسخت بياناته في الأرشيف، ويمكنك إنشاءه من صفحة الأمر بزر «إنشاء PDF»." % (what, res["error"] or "سبب غير معروف")


@route("/orders/new", methods=["GET", "POST"])
@auth.login_required
def order_new():
    if request.method == "GET":
        return render_template("order_new.html", **_new_context({}))
    form = _form_to_dict()
    v, line_items, errors = _read_order(form)
    if form.get("from_quote") == "1" and not form.get("quote_reviewed"):
        errors.append("أكّد مراجعتك للبيانات المقروءة من عرض السعر (علّم المربع الأصفر قبل الحفظ).")
    if errors:
        for e in errors:
            flash(e)
        return render_template("order_new.html", **_new_context(form, next_po=v["po_number"] or next_po_number()))

    now = datetime.now()
    try:
        with db.transaction() as tx:                      # supplier / project / site records, the order and ALL its items - or nothing
            sup_id, supplier, sup_new = masterdata.resolve_supplier(v["supplier"], tx)
            proj_id, project, created = masterdata.resolve_project(v["project"], tx)
            site_id, site, site_new = masterdata.resolve_site(proj_id, v["location"], tx)
            tx.execute(
                "INSERT INTO [PurchaseOrders] ([PONumber], [OrderDate], [HijriDate], [SupplierName], [QuoteNo], [Project], "
                "[Location], [Receiver], [ReceiverPhone], [TotalAmount], [Discount], [ExtraNotes], [HideWarehouseNote], "
                "[ReceiptStatus], [InvoiceStatus], [PaymentStatus], [CreatedAt], [ModifiedAt], [SupplierID], [ProjectID], [SiteID]) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'لم يبدأ', 'معلق', 'معلق', ?, ?, ?, ?, ?)",
                (v["po_number"], v["order_date"], hijri_display(v["order_date"]), supplier, v["quote_no"], project,
                 site or v["location"], v["receiver"], v["phone"], v["total"], v["discount"], v["extra_notes"], v["hide_warehouse_note"], now, now,
                 sup_id, proj_id, site_id),
            )
            _save_items(v["po_number"], line_items, tx)
    except Exception as e:                                # noqa - shown to the person, form kept, nothing half-saved
        flash(_db_failure(e))
        return render_template("order_new.html", **_new_context(form, next_po=v["po_number"]))
    _announce(sup_new and supplier, created and project, site_new and site, sup_id)
    user = (auth.current_user() or {}).get("Username", "")
    folder = archive.ensure_po_folder(v["po_number"])          # الموردين / المورد / المشروع / الموقع / رقم الأمر / {أمر الشراء، المرفقات، النسخ السابقة}
    pending = archive.pending_path(form.get("quote_file_path", "").strip())     # a token (file name), never a path
    if folder and pending:
        try:
            archive.store_document("PO", v["po_number"], archive.po_doc_folder_rel(folder, "عرض السعر"), "عرض السعر", pending,
                                   form.get("quote_file_name", "").strip() or os.path.basename(pending), user=user)
        except (archive.UnsupportedFile, OSError):
            flash("حُفظ الأمر لكن تعذّر حفظ ملف عرض السعر في الأرشيف: أرفقه من صفحة الأمر.")
    remember_receiver(v["receiver"], v["phone"])
    if form.get("compare_id", "").strip().isdigit():                 # the order was created from the award of a quote comparison
        try:
            import compare_service
            compare_service.link_next(int(form["compare_id"]), "أمر شراء", v["po_number"])
        except Exception:                                            # noqa - the order is saved; the link can be typed in the comparison
            logging.getLogger("po.error").exception("comparison link failed")
    flash(_files_message(po_files.save_current(v["po_number"], user), "تم إنشاء أمر الشراء"))
    return redirect(url_for("order_detail", po_number=v["po_number"]))


def _revision_summary(old, old_items, v, line_items):
    changes = []
    new_vals = {"SupplierName": v["supplier"], "Project": v["project"], "Location": v["location"], "QuoteNo": v["quote_no"],
                "Receiver": v["receiver"], "ReceiverPhone": v["phone"], "ExtraNotes": v["extra_notes"]}
    for col, label in ORDER_FIELDS:
        a, b = (old.get(col) or "").strip(), (new_vals[col] or "").strip()
        if a != b:
            changes.append("%s: «%s» ← «%s»" % (label, a[:30], b[:30]))
    if float(old.get("Discount") or 0) != v["discount"]:
        changes.append("الخصم: %s ← %s" % (old.get("Discount") or 0, v["discount"]))
    if old.get("OrderDate") and old["OrderDate"].date() != v["order_date"].date():
        changes.append("التاريخ: %s ← %s" % (old["OrderDate"].strftime("%Y-%m-%d"), v["order_date"].strftime("%Y-%m-%d")))
    before = [(it["ItemName"], it["Unit"] or "", float(it["Qty"] or 0), float(it["UnitPrice"] or 0)) for it in old_items]
    after = [(nm, un, q, p) for nm, un, q, p in line_items]
    if before != after:
        changes.append("الأصناف: %d ← %d" % (len(before), len(after)))
    if round(float(old.get("TotalAmount") or 0), 2) != v["total"]:
        changes.append("الإجمالي: %s ← %s" % ("{:,.2f}".format(old.get("TotalAmount") or 0), "{:,.2f}".format(v["total"])))
    return (" | ".join(changes) or "لا تغيير في البيانات")[:250]


@route("/orders/<po_number>/edit", methods=["GET", "POST"])
@auth.login_required
def order_edit(po_number):
    """Retrieves a saved order for correction. It UPDATES that very order (never creates another). The version being
    replaced is written to the archive first, so an edit can never lose what was there before."""
    old, old_items = po_files.load_order(po_number)
    if not old:
        flash("لم يتم العثور على أمر الشراء.")
        return redirect(url_for("orders_list"))
    if request.method == "GET":
        form = {"po_number": po_number, "supplier": old["SupplierName"] or "", "project": old["Project"] or "",
                "location": old.get("Location") or "", "quote_no": old.get("QuoteNo") or "", "receiver": old.get("Receiver") or "",
                "phone": old.get("ReceiverPhone") or "", "discount": old.get("Discount") or 0, "extra_notes": old.get("ExtraNotes") or "",
                "hide_warehouse_note": bool(old.get("HideWarehouseNote")),
                "order_date": old["OrderDate"].strftime("%Y-%m-%d") if old.get("OrderDate") else "",
                "item_name": [i["ItemName"] for i in old_items], "item_unit": [i["Unit"] or "" for i in old_items],
                "item_qty": [i["Qty"] for i in old_items], "item_price": [i["UnitPrice"] for i in old_items]}
        return render_template("order_new.html", **_new_context(form, edit_po=po_number))

    form = _form_to_dict()
    v, line_items, errors = _read_order(form, editing=po_number)
    if errors:
        for e in errors:
            flash(e)
        return render_template("order_new.html", **_new_context(form, edit_po=po_number))
    user = (auth.current_user() or {}).get("Username", "")
    rev_no = po_files.revision_count(po_number) + 1
    try:                                                  # 1. the safety copy of the version about to be replaced
        snap = po_files.archive_snapshot(po_number, rev_no, old, old_items, user)
    except (OSError, ValueError) as e:
        flash("لم يُحفظ التعديل لأن النسخة الاحتياطية للأمر الحالي تعذّر كتابتها في الأرشيف (%s). لم يتغير شيء." % e)
        return render_template("order_new.html", **_new_context(form, edit_po=po_number))
    try:                                                  # 2. the change itself - all or nothing
        with db.transaction() as tx:
            sup_id, supplier, sup_new = masterdata.resolve_supplier(v["supplier"], tx)
            proj_id, project, created = masterdata.resolve_project(v["project"], tx)
            site_id, site, site_new = masterdata.resolve_site(proj_id, v["location"], tx)
            v["supplier"], v["project"], v["location"] = supplier, project, site or v["location"]
            tx.execute("INSERT INTO [OrderRevisions] ([PONumber], [RevNo], [ChangedAt], [ChangedBy], [Summary], [SnapshotFile], [DataDocID]) "
                       "VALUES (?, ?, Now(), ?, ?, ?, ?)",
                       (po_number, rev_no, user, _revision_summary(old, old_items, v, line_items), snap["RelPath"], snap["DocID"]))
            tx.execute(
                "UPDATE [PurchaseOrders] SET [OrderDate] = ?, [HijriDate] = ?, [SupplierName] = ?, [QuoteNo] = ?, [Project] = ?, "
                "[Location] = ?, [Receiver] = ?, [ReceiverPhone] = ?, [TotalAmount] = ?, [Discount] = ?, [ExtraNotes] = ?, "
                "[HideWarehouseNote] = ?, [ModifiedAt] = ?, [SupplierID] = ?, [ProjectID] = ?, [SiteID] = ? WHERE [PONumber] = ?",
                (v["order_date"], hijri_display(v["order_date"]), supplier, v["quote_no"], project, v["location"], v["receiver"],
                 v["phone"], v["total"], v["discount"], v["extra_notes"], v["hide_warehouse_note"], datetime.now(), sup_id, proj_id, site_id, po_number),
            )
            tx.execute("DELETE FROM [OrderItems] WHERE [PONumber] = ?", (po_number,))
            _save_items(po_number, line_items, tx)
    except Exception as e:                                # noqa - rolled back: the order is exactly as it was
        archive.discard_document(snap["DocID"])
        flash(_db_failure(e))
        return render_template("order_new.html", **_new_context(form, edit_po=po_number))
    _announce(sup_new and supplier, created and project, site_new and site, sup_id)
    want = archive.po_folder_rel(supplier, project, po_number, v["location"])
    notes = []
    if (old.get("ArchiveFolder") or "") and old.get("ArchiveFolder") != want:   # the order's files follow its supplier / project / site
        moved, missing = archive.move_po_folder(po_number, want)
        notes.append("نُقلت ملفات الأمر (%d) إلى مجلده الجديد%s" % (moved, (" - %d ملف مفقود" % missing) if missing else ""))
    remember_receiver(v["receiver"], v["phone"])
    try:                                                  # 3. the old version's PDF, then the new current files
        old_pdf = po_files.archive_previous_pdf(po_number, rev_no, old, old_items, user)
        db.execute("UPDATE [OrderRevisions] SET [PdfDocID] = ? WHERE [PONumber] = ? AND [RevNo] = ?", (old_pdf["DocID"], po_number, rev_no))
    except (pdfgen.PdfError, OSError, ValueError) as e:
        notes.append("تعذّر حفظ PDF النسخة السابقة (%s) لكن بياناتها محفوظة" % e)
    res = po_files.save_current(po_number, user)
    flash(_files_message(res, "تم حفظ التعديل (النسخة الحالية رقم %d)" % (rev_no + 1)) + (" " + "؛ ".join(notes) if notes else ""))
    return redirect(url_for("order_detail", po_number=po_number))


@route("/orders/new/parse-quote", methods=["POST"])
@auth.login_required
def order_parse_quote():
    """Reads an uploaded quote (PDF/Word/Excel/image) with quote2po - in-process, no subprocess - and re-renders the
    new-order form pre-filled with what it found, plus a yellow review box: the name read is matched with the registered
    suppliers, and nothing can be saved until the person ticks that the figures were checked."""
    import quote2po

    form = _form_to_dict()
    warnings = []
    quote_file = request.files.get("quote_file")

    def page():
        return render_template("order_new.html", **_new_context(form, warnings=warnings))

    if not quote_file or not quote_file.filename:
        flash("اختر ملف عرض السعر أولاً.")
        return page()
    try:
        token = archive.save_pending(quote_file)
    except archive.UnsupportedFile:
        flash("صيغة الملف غير مدعومة (المسموح: PDF، Word، Excel، صور).")
        return page()
    tmp_path = archive.pending_path(token)
    form["quote_file_path"] = token
    form["quote_file_name"] = quote_file.filename
    form["from_quote"] = "1"
    form["quote_reviewed"] = ""

    def offer_manual_copy(md_text):
        """When automatic extraction isn't usable, writes the quote into a plain .xlsx next to the upload so the
        person can open it and copy the figures by hand into the items table below."""
        xlsx_name = os.path.splitext(token)[0] + "_نسخة_يدوية.xlsx"
        xlsx_path = os.path.join(os.path.dirname(tmp_path), xlsx_name)
        try:
            if quote2po.md_table_to_xlsx(md_text, xlsx_path):
                form["quote_xlsx_name"] = xlsx_name
                flash("أُنشئت نسخة Excel من عرض السعر يمكنك نسخ الأرقام منها يدويًا.")
        except Exception:
            pass  # the manual-copy file is a convenience, not worth failing the page over

    md = None
    try:
        md, engine = quote2po.convert_to_md(tmp_path)
        res = quote2po.parse_quote(md)
        if engine == "ocr":
            warnings.append("قُرئ الملف بالتعرف الضوئي (OCR): تأكد من كل رقم واسم، فقد تحدث أخطاء في الحروف والأرقام.")
        read_name = res.get("supplier")
        if read_name:
            match, score = suggest_supplier(read_name)
            if match and score >= 0.999:
                form["supplier"] = match
            elif match:
                form["supplier"] = match
                warnings.append("الاسم المقروء من العرض «%s» يشبه المورد المسجّل «%s» فاستُخدم المسجّل. تأكد أنه الصحيح." % (read_name, match))
            else:
                form["supplier"] = read_name
                warnings.append("الاسم المقروء «%s» لا يطابق أي مورد مسجّل. اختر المورد الصحيح من القائمة أو أضفه من تاب الموردين." % read_name)
        else:
            warnings.append("لم يُقرأ اسم المورد من العرض. اخترْه من القائمة.")
        if res.get("quote_no"):
            form["quote_no"] = res["quote_no"]
        if res["items"]:
            form["item_name"] = [it["name"] for it in res["items"]]
            form["item_unit"] = [it.get("unit", "") for it in res["items"]]
            form["item_qty"] = [it["qty"] for it in res["items"]]
            form["item_price"] = [it["price"] for it in res["items"]]
            no_price = [it["name"][:30] for it in res["items"] if not it["price"] or not it["qty"]]
            if no_price:
                warnings.append("أصناف بلا كمية أو سعر: %s" % "، ".join(no_price[:5]))
            flash("تم استخراج %d صنف من عرض السعر - راجع البيانات قبل الحفظ." % len(res["items"]))
        else:
            flash("تم رفع الملف لكن تعذّر استخراج الأصناف تلقائياً منه. أدخلها يدوياً.")
            warnings.append("لم تُستخرج أصناف: أدخلها يدويًا (أو استخدم نسخة Excel الجاهزة للنسخ).")
            offer_manual_copy(md)
    except quote2po.NeedsAI:
        flash("الملف صورة ممسوحة أو نصّه غير قابل للقراءة، ولا يتوفر برنامج التعرف الضوئي (Tesseract). أدخل الأصناف يدوياً.")
        warnings.append("لتفعيل قراءة الصور والملفات الممسوحة ثبّت Tesseract بحزمة العربية (راجع الدليل) ثم حدّد مساره في config.ini.")
    except quote2po.MissingLibrary as e:
        flash(quote2po.missing_lib_message(str(e)))
    except Exception as e:
        flash("تعذّر قراءة الملف تلقائياً: %s" % e)
        if md:
            offer_manual_copy(md)
    return page()

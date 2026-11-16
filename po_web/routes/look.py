# -*- coding: utf-8 -*-
"""Version 29.0 settings pages: «المظهر» (the look of the system) and «إعدادات الطباعة» (each printed document), and «التحقق من مستند»
(the six-character code printed with the QR code)."""
from flask import flash, jsonify, redirect, render_template, request, url_for

import auth
import audit
import print_settings
import ui_theme
from routes import route


# --------------------------------------------------------------------------- the look
@route("/settings/appearance", methods=["GET", "POST"])
@auth.login_required
def settings_appearance():
    if request.method == "POST":
        if request.form.get("reset"):
            s = ui_theme.save(dict(ui_theme.DEFAULT))
            flash("رجع المظهر إلى الإعدادات الأصلية.")
        else:
            s = ui_theme.save({k: request.form.get(k, "") for k in ("preset", "brand", "font", "size", "density")})
            flash("حُفظ المظهر، ويظهر الآن لكل المستخدمين: %s" % ui_theme.describe(s))
        audit.detail(ui_theme.describe(s))
        return redirect(url_for("settings_appearance"))
    s = ui_theme.get()
    presets = [dict(key=k, tokens=ui_theme.tokens(dict(s, preset=k, brand=s["brand"] or "#7A3E2C")), **v) for k, v in ui_theme.PRESETS.items()]
    return render_template("settings_appearance.html", active="settings_appearance", tab="appearance", s=s, presets=presets,
                           fonts=ui_theme.FONTS, sizes=ui_theme.SIZES, density=ui_theme.DENSITY)


@route("/settings/appearance/tokens")
@auth.login_required
def settings_appearance_tokens():
    """The tokens of a choice not saved yet - the page previews itself with them."""
    raw = {k: request.args.get(k, "") for k in ("preset", "brand", "font", "size", "density")}
    s = ui_theme.clean(raw)
    note = ""
    if s["preset"] == "brand" and s["brand"]:
        _c, darker = ui_theme.readable(s["brand"])
        note = "غُمِّق اللون في الأزرار حتى تبقى الكتابة البيضاء مقروءة." if darker else "اللون مقروء كما هو."
    return jsonify({"tokens": ui_theme.tokens(s), "note": note})


# --------------------------------------------------------------------------- printing
@route("/settings/print", methods=["GET", "POST"])
@auth.login_required
def settings_print():
    doc = request.values.get("doc", "po")
    if doc not in print_settings.DOCS:
        doc = "po"
    if request.method == "POST":
        if request.form.get("reset"):
            s = print_settings.save(doc, {})
            flash("رجعت إعدادات طباعة «%s» إلى الأصل." % print_settings.DOCS[doc]["name"])
        else:
            s = print_settings.save(doc, print_settings.from_form(request.form))
            flash("حُفظت إعدادات طباعة «%s»." % print_settings.DOCS[doc]["name"])
        audit.detail(print_settings.describe(s))
        return redirect(url_for("settings_print", doc=doc))
    return render_template("settings_print.html", active="settings_print", tab="print", doc=doc, docs=print_settings.DOCS,
                           s=print_settings.get(doc), opts=print_settings.OPTIONS, flags=print_settings.FLAGS, sample=_sample(doc))


def _sample(doc):
    """(link, what) to the last real document of this kind - to see the settings on a real paper. None when there is none yet."""
    import db
    try:
        if doc == "po":
            r = db.query("SELECT TOP 1 [PONumber] FROM [PurchaseOrders] ORDER BY [CreatedAt] DESC")
            return (url_for("order_print", po_number=r[0]["PONumber"]), "أمر الشراء " + r[0]["PONumber"]) if r else None
        if doc == "voucher":
            r = db.query("SELECT TOP 1 [VoucherID], [VoucherNo] FROM [PaymentVouchers] ORDER BY [VoucherID] DESC")
            return (url_for("voucher_print", voucher_id=r[0]["VoucherID"]), "أمر الصرف " + r[0]["VoucherNo"]) if r else None
        if doc in ("cycle", "payorder"):
            r = db.query("SELECT TOP 1 [CycleID], [CycleNo] FROM [PaymentCycles] ORDER BY [CycleID] DESC")
            if not r:
                return None
            if doc == "cycle":
                return url_for("cycle_print", cycle_id=r[0]["CycleID"]), "كشف الدورة رقم %s" % r[0]["CycleNo"]
            return url_for("cycle_orders_print", cycle_id=r[0]["CycleID"]), "أوامر صرف الدورة رقم %s" % r[0]["CycleNo"]
        if doc == "statement":
            r = db.query("SELECT TOP 1 [SubcontractorID], [Name] FROM [Subcontractors] ORDER BY [SubcontractorID]")
            return (url_for("statement_print", sub_id=r[0]["SubcontractorID"]), "كشف حساب " + r[0]["Name"]) if r else None
        if doc == "supplier_statement":
            r = db.query("SELECT TOP 1 [SupplierID], [Name] FROM [Suppliers] ORDER BY [SupplierID]")
            return (url_for("supplier_statement_print", supplier_id=r[0]["SupplierID"]), "كشف حساب " + r[0]["Name"]) if r else None
    except Exception:                                   # noqa - no sample is not an error
        return None
    return None


# --------------------------------------------------------------------------- checking a printed paper
@route("/verify", methods=["GET", "POST"])
@auth.login_required
def verify_document():
    result = None
    no = (request.values.get("no") or "").strip()
    code = (request.values.get("code") or "").strip()
    if no and code:
        result = print_settings.verify(no, code, (request.values.get("amount") or "").replace(",", "").strip() or None)
    return render_template("verify.html", active="verify", no=no, code=code, result=result)

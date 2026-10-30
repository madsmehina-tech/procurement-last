# -*- coding: utf-8 -*-
"""The «الطباعة» tab: general payment vouchers (أمر صرف عام, PR-0027 ...). See voucher_service for the rules."""
import io
import logging
from datetime import datetime

from flask import flash, jsonify, redirect, render_template, request, send_file, url_for

import auth
import contract_gen as cg
import cycle_files
import voucher_service as vs
from routes import route


def _user():
    return (auth.current_user() or {}).get("Username", "")


def _form_page(v, errors=None, voucher=None):
    return render_template("voucher_form.html", v=v, voucher=voucher, projects=vs.projects(), pay_methods=vs.PAY_METHODS, ref_types=vs.REF_TYPES, modes=vs.MODES,
                           sign_fields=vs.SIGN_FIELDS, errors=errors or [])


def _show_data(d):
    """The parsed form turned back into what the form displays (after a validation error nothing the person typed is lost)."""
    out = dict(d)
    out["items"] = [{"desc": i.get("desc", ""), "qty": i.get("qty", ""), "price": i.get("price", "")} for i in d.get("items", [])]
    out["discount"] = d.get("discount") or ""
    return out


@route("/vouchers")
@auth.login_required
def vouchers_list():
    status, q = request.args.get("status", ""), request.args.get("q", "").strip()
    rows = vs.listing(status, q)
    allrows = vs.listing()
    kpi = {"count": len(allrows), "draft": sum(1 for r in allrows if r["Status"] == vs.ST_DRAFT), "approved": sum(1 for r in allrows if r["Status"] == vs.ST_APPROVED),
           "net": round(sum(float(r.get("Net") or 0) for r in allrows if r["Status"] == vs.ST_APPROVED), 2)}
    return render_template("voucher_list.html", rows=rows, kpi=kpi, status=status, q=q, statuses=(vs.ST_DRAFT, vs.ST_APPROVED, vs.ST_CANCELLED),
                           next_no="PR-%04d" % (vs.last_number() + 1), can_set_start=(kpi["count"] == 0))


@route("/vouchers/start-number", methods=["POST"])
@auth.login_required
def voucher_start_no():
    try:
        vs.set_start_number(request.form.get("first_no") or 0)
        flash("سيبدأ الترقيم من PR-%04d." % int(request.form["first_no"]))
    except (vs.VoucherError, ValueError) as e:
        flash(str(e) if isinstance(e, vs.VoucherError) else "اكتب رقماً صحيحاً.")
    return redirect(url_for("vouchers_list"))


@route("/vouchers/new", methods=["GET", "POST"])
@auth.login_required
def voucher_new():
    if request.method == "GET":
        return _form_page(vs.defaults(cg.company()))
    d, errors = vs.parse(request.form)
    if errors:
        for e in errors:
            flash(e)
        return _form_page(_show_data(d), errors)
    try:
        vid, number = vs.create(d, _user())
    except Exception as e:                                   # noqa
        logging.getLogger("po.error").exception("voucher save failed")
        flash("تعذّر حفظ أمر الصرف ولم يتغير شيء. السبب: %s" % " ".join(str(e).split())[:200])
        return _form_page(_show_data(d))
    if request.form.get("action") == "approve" and auth.can("vouchers.approve"):          # «حفظ واعتماد»: one click, but the approval can still be refused
        try:
            flash("تم إنشاء أمر الصرف %s. %s" % (number, vs.approve(vid, _user())))
        except vs.VoucherError as e:
            flash("حُفظ أمر الصرف %s كمسودة لكن لم يُعتمد: %s" % (number, e))
        return redirect(url_for("voucher_view", voucher_id=vid))
    flash("تم إنشاء أمر الصرف %s كمسودة. راجعه ثم اعتمده أو اطبعه." % number)
    return redirect(url_for("voucher_view", voucher_id=vid))


def _stored_to_form(v):
    import json
    items = json.loads(v.get("ItemsJson") or "[]")
    sign = json.loads(v.get("SignJson") or "{}")
    return {"voucher_date": v["VoucherDate"].strftime("%Y-%m-%d") if v.get("VoucherDate") else "", "project_id": v.get("ProjectID") or "", "party_kind": "sup" if v.get("PartyKind") == "مورد" else "sub",
            "party_id": v.get("SupplierID") or v.get("SubcontractorID") or "", "party_name": v.get("PartyName"), "request_type": v.get("RequestType") or "", "department": v.get("Department") or "",
            "cost_center": v.get("CostCenter") or "", "pay_method": v.get("PayMethod"), "pay_statement": v.get("PayStatement") or "", "description": v.get("Description") or "",
            "amount_mode": v.get("AmountMode") or "incl", "vat_pct": "%d" % round(v.get("VatPct") or 0), "discount": v.get("Discount") or "",
            "show_totals": bool(v.get("ShowTotals")), "post_choice": v.get("PostChoice") or vs.NO,
            "items": [{"desc": i["desc"], "qty": 1, "price": i.get("total", i["price"])} for i in items], "refs": json.loads(v.get("RefsJson") or "[]"), "notes": v.get("Notes") or "", "sign": sign}


@route("/vouchers/<int:voucher_id>/edit", methods=["GET", "POST"])
@auth.login_required
def voucher_edit(voucher_id):
    v = vs.get(voucher_id)
    if not v:
        flash("لم يتم العثور على أمر الصرف.")
        return redirect(url_for("vouchers_list"))
    if v["Status"] != vs.ST_DRAFT:
        flash("لا يُعدَّل أمر الصرف إلا وهو «مسودة».")
        return redirect(url_for("voucher_view", voucher_id=voucher_id))
    if request.method == "GET":
        return _form_page(_stored_to_form(v), voucher=v)
    d, errors = vs.parse(request.form)
    if errors:
        for e in errors:
            flash(e)
        return _form_page(_show_data(d), errors, voucher=v)
    try:
        vs.update(voucher_id, d, _user())
    except vs.VoucherError as e:
        flash(str(e))
        return redirect(url_for("voucher_view", voucher_id=voucher_id))
    if request.form.get("action") == "approve" and auth.can("vouchers.approve"):
        try:
            flash("حُفظ التعديل. %s" % vs.approve(voucher_id, _user()))
        except vs.VoucherError as e:
            flash("حُفظ التعديل لكن لم يُعتمد: %s" % e)
        return redirect(url_for("voucher_view", voucher_id=voucher_id))
    flash("حُفظ التعديل.")
    return redirect(url_for("voucher_view", voucher_id=voucher_id))


@route("/vouchers/<int:voucher_id>")
@auth.login_required
def voucher_view(voucher_id):
    v = vs.get(voucher_id)
    if not v:
        flash("لم يتم العثور على أمر الصرف.")
        return redirect(url_for("vouchers_list"))
    return render_template("voucher_view.html", **vs.print_context(v, cg.company()))


@route("/vouchers/<int:voucher_id>/approve", methods=["POST"])
@auth.login_required
def voucher_approve(voucher_id):
    try:
        flash(vs.approve(voucher_id, _user()))
    except vs.VoucherError as e:
        flash(str(e))
    except Exception as e:                                   # noqa - the approval and the payment are one transaction: nothing was saved
        logging.getLogger("po.error").exception("voucher approve failed")
        flash("تعذّر الاعتماد ولم يتغير شيء. السبب: %s" % " ".join(str(e).split())[:200])
    return redirect(url_for("voucher_view", voucher_id=voucher_id))


@route("/vouchers/<int:voucher_id>/cancel", methods=["POST"])
@auth.login_required
def voucher_cancel(voucher_id):
    try:
        vs.cancel(voucher_id, _user())
        flash("أُلغي أمر الصرف (يبقى في السجل ولا يُحذف).")
    except vs.VoucherError as e:
        flash(str(e))
    return redirect(url_for("voucher_view", voucher_id=voucher_id))


def _print_html(voucher_id):
    v = vs.get(voucher_id)
    return render_template("voucher_print.html", **vs.print_context(v, cg.company())) if v else None


@route("/vouchers/<int:voucher_id>/print")
@auth.login_required
def voucher_print(voucher_id):
    html = _print_html(voucher_id)
    return html if html else redirect(url_for("vouchers_list"))


@route("/vouchers/<int:voucher_id>/pdf")
@auth.login_required
def voucher_pdf(voucher_id):
    html = _print_html(voucher_id)
    if not html:
        return redirect(url_for("vouchers_list"))
    v = vs.get(voucher_id)
    try:
        pdf = cycle_files._pdf_bytes(html)
    except cycle_files.pdfgen.PdfError as e:
        flash("تعذّر إنشاء PDF تلقائيًا (%s) - استخدم «طباعة» ثم «حفظ كـ PDF» من المتصفح." % e)
        return redirect(url_for("voucher_view", voucher_id=voucher_id))
    return send_file(io.BytesIO(pdf), as_attachment=True, download_name="%s.pdf" % v["VoucherNo"], mimetype="application/pdf")


# --------------------------------------------------------------------------- the lists the form is linked to (JSON for the drop-downs)
@route("/vouchers/api/parties")
@auth.login_required
def voucher_api_parties():
    kind = "sub" if request.args.get("kind") == "sub" else "sup"
    return jsonify(vs.parties(kind, request.args.get("project", "")))


@route("/vouchers/api/documents")
@auth.login_required
def voucher_api_documents():
    kind, pid = ("sub" if request.args.get("kind") == "sub" else "sup"), request.args.get("id", "")
    if not pid.isdigit():
        return jsonify([])
    return jsonify(vs.documents(kind, pid, request.args.get("project", "")))

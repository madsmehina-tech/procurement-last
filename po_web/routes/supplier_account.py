# -*- coding: utf-8 -*-
"""The supplier's account (statement + movements typed by hand) and the OPENING BALANCES of contractors and suppliers."""
import io
import logging
from datetime import datetime

from flask import flash, redirect, render_template, request, send_file, url_for

import auth
import contract_gen as cg
import cycle_files
import db
import money_guards as mg
import opening_service as op
import supplier_account as sa
from routes import route


def _user():
    return (auth.current_user() or {}).get("Username", "")


def _day(raw):
    try:
        return datetime.strptime((raw or "").strip(), "%Y-%m-%d")
    except ValueError:
        return None


def _back_sup(supplier_id):
    return redirect(url_for("supplier_detail", supplier_id=supplier_id) + "#tab-account")


def _back_sub(sub_id):
    return redirect(url_for("subcontractor_detail", sub_id=sub_id) + "#tab-ledger")


# --------------------------------------------------------------------------- the statement
def _read(supplier_id):
    a = request.args
    basis = a.get("basis") if a.get("basis") in ("orders", "invoiced") else "orders"
    d_from, d_to, project = _day(a.get("date_from")), _day(a.get("date_to")), a.get("project", "").strip()
    data = sa.statement(supplier_id, d_from, d_to, project, basis)
    if not data:
        return None, None
    period = []
    if a.get("date_from"):
        period.append("من %s" % a.get("date_from"))
    if a.get("date_to"):
        period.append("إلى %s" % a.get("date_to"))
    if project:
        period.append("مشروع: " + project)
    period.append("المشتريات: " + ("الفواتير الصادرة فقط" if basis == "invoiced" else "كل أوامر الشراء"))
    return data, dict(date_from=a.get("date_from", ""), date_to=a.get("date_to", ""), project=project, basis=basis, label=" · ".join(period))


@route("/suppliers/<int:supplier_id>/statement")
@auth.login_required
def supplier_statement(supplier_id):
    data, f = _read(supplier_id)
    if not data:
        flash("لم يتم العثور على المورد.")
        return redirect(url_for("suppliers_list"))
    return render_template("supplier_statement.html", d=data, f=f, supplier=data["supplier"])


def _print_html(supplier_id):
    data, f = _read(supplier_id)
    if not data:
        return None
    return render_template("supplier_statement_print.html", d=data, f=f, supplier=data["supplier"], now=datetime.now().strftime("%Y-%m-%d %H:%M"),
                           company=cg.company())


@route("/suppliers/<int:supplier_id>/statement/print")
@auth.login_required
def supplier_statement_print(supplier_id):
    html = _print_html(supplier_id)
    return html if html else redirect(url_for("suppliers_list"))


@route("/suppliers/<int:supplier_id>/statement/export.pdf")
@auth.login_required
def supplier_statement_pdf(supplier_id):
    html = _print_html(supplier_id)
    if not html:
        return redirect(url_for("suppliers_list"))
    try:
        pdf = cycle_files._pdf_bytes(html)
    except cycle_files.pdfgen.PdfError as e:
        flash("تعذّر إنشاء PDF تلقائيًا (%s) - استخدم «طباعة» ثم «حفظ كـ PDF» من المتصفح." % e)
        return redirect(url_for("supplier_statement", supplier_id=supplier_id, **request.args))
    return send_file(io.BytesIO(pdf), as_attachment=True, download_name="supplier_statement_%s_%s.pdf" % (supplier_id, datetime.now().strftime("%Y%m%d")), mimetype="application/pdf")


# --------------------------------------------------------------------------- movements typed by hand
@route("/suppliers/<int:supplier_id>/entries/new", methods=["POST"])
@auth.login_required
def supplier_entry_add(supplier_id):
    f = request.form
    if not db.query("SELECT [SupplierID] FROM [Suppliers] WHERE [SupplierID] = ?", (supplier_id,)):
        flash("لم يتم العثور على المورد.")
        return redirect(url_for("suppliers_list"))
    try:
        amount = mg.parse_money(f.get("amount"), allow_zero=False, label="المبلغ")
        sa.add_entry(supplier_id, f.get("kind"), f.get("category"), f.get("description"), amount, _day(f.get("entry_date")) or datetime.now(),
                     f.get("project"), f.get("po_number"), f.get("bank_ref"), _user())
    except (mg.MoneyError, sa.SupplierAccountError) as e:
        flash(str(e))
        return _back_sup(supplier_id)
    except Exception as e:                                   # noqa
        logging.getLogger("po.error").exception("supplier entry save failed")
        flash("تعذّر حفظ الحركة ولم يتغير شيء. السبب: %s" % " ".join(str(e).split())[:200])
        return _back_sup(supplier_id)
    flash("سُجّلت الحركة وظهرت في كشف حساب المورد.")
    return _back_sup(supplier_id)


@route("/suppliers/entries/<int:entry_id>/delete", methods=["POST"])
@auth.login_required
def supplier_entry_delete(entry_id):
    try:
        supplier_id = sa.delete_entry(entry_id)
    except sa.SupplierAccountError as e:
        flash(str(e))
        return redirect(url_for("suppliers_list"))
    flash("حُذفت الحركة.")
    return _back_sup(supplier_id)


# --------------------------------------------------------------------------- opening balances
def _opening_args(f):
    amount = mg.parse_money(f.get("amount"), allow_zero=False, label="مبلغ رصيد الافتتاح")
    when = _day(f.get("as_of"))
    if not when:
        raise op.OpeningError("اكتب تاريخ رصيد الافتتاح (الرصيد كما في هذا التاريخ).")
    return f.get("direction"), amount, when, f.get("note")


@route("/subcontractors/<int:sub_id>/opening", methods=["POST"])
@auth.login_required
def sub_opening_save(sub_id):
    if not db.query("SELECT [SubcontractorID] FROM [Subcontractors] WHERE [SubcontractorID] = ?", (sub_id,)):
        flash("لم يتم العثور على المقاول.")
        return redirect(url_for("subcontractors_list"))
    try:
        direction, amount, when, note = _opening_args(request.form)
        op.set_sub_opening(sub_id, direction, amount, when, note, _user())
    except (mg.MoneyError, op.OpeningError) as e:
        flash(str(e))
        return _back_sub(sub_id)
    flash("حُفظ رصيد الافتتاح وصار أول سطر في كشف الحساب.")
    return _back_sub(sub_id)


@route("/subcontractors/<int:sub_id>/opening/clear", methods=["POST"])
@auth.login_required
def sub_opening_clear(sub_id):
    op.clear_sub_opening(sub_id)
    flash("أُلغي رصيد الافتتاح.")
    return _back_sub(sub_id)


@route("/suppliers/<int:supplier_id>/opening", methods=["POST"])
@auth.login_required
def supplier_opening_save(supplier_id):
    if not db.query("SELECT [SupplierID] FROM [Suppliers] WHERE [SupplierID] = ?", (supplier_id,)):
        flash("لم يتم العثور على المورد.")
        return redirect(url_for("suppliers_list"))
    try:
        direction, amount, when, note = _opening_args(request.form)
        op.set_supplier_opening(supplier_id, direction, amount, when, note, _user())
    except (mg.MoneyError, op.OpeningError) as e:
        flash(str(e))
        return _back_sup(supplier_id)
    flash("حُفظ رصيد الافتتاح وصار أول سطر في كشف حساب المورد.")
    return _back_sup(supplier_id)


@route("/suppliers/<int:supplier_id>/opening/clear", methods=["POST"])
@auth.login_required
def supplier_opening_clear(supplier_id):
    op.clear_supplier_opening(supplier_id)
    flash("أُلغي رصيد الافتتاح.")
    return _back_sup(supplier_id)


@route("/settings/opening-balances", methods=["GET", "POST"])
@auth.login_required
def opening_balances():
    """All contractors and suppliers on one page: type each opening balance once, with ONE common as-of date (the migration day)."""
    if request.method == "POST":
        f = request.form
        when = _day(f.get("as_of"))
        if not when:
            flash("اكتب تاريخ رصيد الافتتاح (يوم بدء العمل بالنظام) ثم احفظ.")
            return redirect(url_for("opening_balances"))
        done, problems = 0, []
        for key in f.getlist("party"):
            raw = f.get("amount_" + key, "").strip()
            if not raw:
                continue                                      # empty = leave this party as it is
            kind, _, pid = key.partition(":")
            if kind not in ("sub", "sup") or not pid.isdigit():
                continue
            try:
                amount = mg.parse_money(raw, allow_zero=False, label="المبلغ")
                direction = f.get("dir_" + key) or op.DIR_OWE_PARTY
                (op.set_sub_opening if kind == "sub" else op.set_supplier_opening)(int(pid), direction, amount, when, f.get("note_" + key), _user())
                done += 1
            except (mg.MoneyError, op.OpeningError) as e:
                problems.append("%s: %s" % (f.get("name_" + key, key), e))
        flash("حُفظت أرصدة الافتتاح لـ %d طرفاً." % done if done else "لم يُدخل أي رصيد جديد.")
        for p in problems[:8]:
            flash("لم يُحفظ — " + p)
        return redirect(url_for("opening_balances"))
    return render_template("opening_balances.html", parties=op.all_parties(), today=datetime.now().strftime("%Y-%m-%d"), labels=op.LABEL)

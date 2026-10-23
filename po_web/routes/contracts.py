# -*- coding: utf-8 -*-
"""Contracts: the contracts tab (all contracts of all subcontractors), a contract's page, create / edit / sites / file."""
import logging
from datetime import datetime, timedelta

from flask import abort, flash, redirect, render_template, request, url_for

import archive
import auth
import money_guards as mg
import contracts_service as cs
import db
import masterdata
import projects as project_list
from routes import route
from textnorm import clean_spaces, contains, norm_key

CONTRACT_STATUSES = ["مسودة", "صادر", "ساري", "موقوف", "منتهي", "مفسوخ"]
ARCHIVED_STATUSES = ["ساري", "موقوف", "منتهي", "مفسوخ"]


def _project_choices():
    """Project names for the form: the unified list (falls back to the names typed on past orders before migrate.py ran)."""
    names = project_list.names() or sorted({r["Project"] for r in db.query("SELECT DISTINCT [Project] FROM [PurchaseOrders]") if r["Project"]})
    return [{"Project": n} for n in names]


def _float(name, label="القيمة", maximum=None):
    v = request.form.get(name, "").strip()
    return mg.parse_money(v, label=label, maximum=maximum or mg.MAX_MONEY) if v else None


@route("/contracts")
@auth.login_required
def contracts_list():
    """The central register of contracts and agreements: contractors AND suppliers, signed outside the system (مؤرشف) or made by it (مولَّد)."""
    a = request.args
    q, f_project, f_status = a.get("q", "").strip(), a.get("project", "").strip(), a.get("status", "").strip()
    f_sub, f_sup = a.get("sub", type=int), a.get("sup", type=int)
    f_kind, f_type, f_origin = a.get("kind", "").strip(), a.get("type", "").strip(), a.get("origin", "").strip()
    d_from, d_to = _day(a.get("date_from")), _day(a.get("date_to"))
    subs, contracts, certs, _p = cs.load()
    sups = {s["SupplierID"]: s["Name"] for s in db.query("SELECT [SupplierID], [Name] FROM [Suppliers]")}
    rows = []
    for c in contracts.values():
        kind = c.get("PartyKind") or "مقاول"
        party = sups.get(c.get("SupplierID"), "") if kind == "مورد" else (subs.get(c.get("SubcontractorID")) or {}).get("Name", "")
        when = c.get("ContractDate") or c.get("StartDate") or c.get("CreatedAt")
        if (f_sub and c.get("SubcontractorID") != f_sub) or (f_sup and c.get("SupplierID") != f_sup) or (f_kind and kind != f_kind):
            continue
        if (f_project and c.get("Project") != f_project) or (f_status and (c.get("Status") or "ساري") != f_status):
            continue
        if (f_type and (c.get("ContractType") or "عقد مقاول باطن") != f_type) or (f_origin and (c.get("Origin") or "مؤرشف") != f_origin):
            continue
        if (d_from and (not when or when < d_from)) or (d_to and (not when or when >= d_to + timedelta(days=1))):
            continue
        if q and not any(contains(x or "", q) for x in (c.get("ContractNo"), party, c.get("Project"), c.get("SiteName"), c.get("ContractType"))):
            continue
        rows.append(dict(c, SubName=party, Kind=kind, When=when, Counts=cs.counts(c), **cs.contract_figures(c, certs)))
    rows.sort(key=lambda r: r.get("CreatedAt") or datetime.min, reverse=True)
    live = [r for r in rows if r["Counts"]]
    active = [r for r in live if (r.get("Status") or "ساري") == "ساري"]
    kpi = {"value": sum(r["value"] for r in live), "executed": sum(r["executed"] for r in live), "active": len(active), "over90": sum(1 for r in live if r["pct"] is not None and r["pct"] >= 90),
           "drafts": sum(1 for r in rows if (r.get("Status") or "") in ("مسودة", "صادر")), "generated": sum(1 for r in rows if r.get("Origin") == "مولَّد")}
    types = sorted({(c.get("ContractType") or "عقد مقاول باطن") for c in contracts.values()})
    return render_template("contracts_list.html", rows=rows, kpi=kpi, q=q, f_sub=f_sub, f_sup=f_sup, f_project=f_project, f_status=f_status, f_kind=f_kind, f_type=f_type, f_origin=f_origin,
                           date_from=a.get("date_from", ""), date_to=a.get("date_to", ""), subs=sorted(subs.values(), key=lambda s: s["Name"]), sups=sorted(sups.items(), key=lambda kv: kv[1]), types=types,
                           projects=sorted({c.get("Project") for c in contracts.values() if c.get("Project")}), statuses=CONTRACT_STATUSES)


def _day(raw):
    try:
        return datetime.strptime((raw or "").strip(), "%Y-%m-%d")
    except ValueError:
        return None


def _files_of(name):
    return [f for f in request.files.getlist(name) if f and f.filename]


def _bad_file(files):
    """The first file whose type is not accepted (checked BEFORE anything is saved)."""
    return next((f.filename for f in files if archive._ext(f.filename) not in archive.ALLOWED_EXT), None)


def _back(sub_id, **kw):
    if not sub_id:                                         # a supplier agreement has no contractor page: back to the register
        return redirect(url_for("contracts_list"))
    return redirect(url_for("subcontractor_detail", sub_id=sub_id, **kw) + "#contracts")


@route("/subcontractors/<int:sub_id>/contracts/new", methods=["GET", "POST"])
@auth.login_required
def contract_new(sub_id):
    """The contract form opens from the contractor's page and returns to it after saving."""
    import routes.subcontractors as subs_routes
    rows = db.query("SELECT * FROM [Subcontractors] WHERE [SubcontractorID] = ?", (sub_id,))
    if not rows:
        flash("لم يتم العثور على المقاول.")
        return redirect(url_for("subcontractors_list"))
    sub = rows[0]
    if request.method == "GET":
        return _back(sub_id, new_contract=1)

    def again():
        return subs_routes.render_sub_page(sub_id, open_dialog="contract-new", form=request.form)

    project = clean_spaces(request.form.get("project", ""))
    site_name = clean_spaces(request.form.get("site_name", ""))
    contract_no = clean_spaces(request.form.get("contract_no", ""))
    if not project or not site_name:
        flash("اسم المشروع واسم الموقع الأول مطلوبان.")
        return again()
    try:
        contract_value, retention_pct, advance_pct = _float("contract_value", "قيمة العقد"), _float("retention_pct", "نسبة الاستقطاع", 100), _float("advance_pct", "نسبة الدفعة المقدمة", 100)
    except ValueError:
        flash("القيم المرجعية (قيمة العقد/النسب) يجب أن تكون أرقامًا إن أُدخلت.")
        return again()
    if contract_no and any(norm_key(c.get("ContractNo") or "") == norm_key(contract_no) for c in db.query("SELECT [ContractNo] FROM [Contracts]")):
        flash("رقم العقد «%s» مستخدم لعقد آخر." % contract_no)
        return again()
    files = _files_of("contract_file") + _files_of("extra_files")
    if _bad_file(files):
        flash("صيغة الملف غير مدعومة: %s (المسموح: PDF، صور، Word، Excel)." % _bad_file(files))
        return again()
    notes = request.form.get("notes", "").strip()
    try:
        with db.transaction() as tx:                       # project / site records, the contract and its first site - or nothing
            proj_id, project, project_created = masterdata.resolve_project(project, tx)
            site_id, site_name, site_created = masterdata.resolve_site(proj_id, site_name, tx)
            contract_id = tx.execute_insert(
                "INSERT INTO [Contracts] ([SubcontractorID], [Project], [SiteName], [Status], [ContractValue], [RetentionPct], "
                "[AdvancePct], [Notes], [ContractNo], [CreatedAt], [ProjectID]) VALUES (?, ?, ?, 'ساري', ?, ?, ?, ?, ?, ?, ?)",
                (sub_id, project, site_name, contract_value, retention_pct, advance_pct, notes, contract_no or None, datetime.now(), proj_id),
            )
            tx.execute("INSERT INTO [ContractSites] ([ContractID], [SiteName], [CreatedAt], [SiteRefID]) VALUES (?, ?, ?, ?)",
                       (contract_id, site_name, datetime.now(), site_id))
    except Exception as e:                                 # noqa - nothing half-saved, the person is told why
        logging.getLogger("po.error").exception("contract save failed")
        flash("تعذّر حفظ العقد ولم يتغير أي شيء. السبب: %s" % " ".join(str(e).split())[:200])
        return again()
    archive.ensure_sub_folder(sub_id)
    saved = _store_contract_files(contract_id, _files_of("contract_file"), "العقد الموقّع") + _store_contract_files(contract_id, _files_of("extra_files"), "مرفق إضافي")
    if project_created:
        flash("أُضيف مشروع جديد إلى القائمة: %s" % project)
    if site_created:
        flash("أُضيف موقع جديد للمشروع: %s" % site_name)
    flash("تم حفظ العقد وأُرشف في: %s\\العقود\\عقد رقم %s\\ملفات العقد%s" % (sub["Name"], archive.contract_label(contract_no, contract_id), " (%d ملف)" % saved if saved else " - لم يُرفع ملف بعد"))
    return _back(sub_id)


def _store_contract_files(contract_id, files, doc_type):
    n = 0
    folder = archive.ensure_contract_folder(contract_id)
    user = (auth.current_user() or {}).get("Username", "")
    for f in files:
        try:
            archive.store_document("CONTRACT", contract_id, folder, doc_type, f, f.filename, "", user)
            n += 1
        except (archive.UnsupportedFile, OSError):
            flash("تعذّر حفظ الملف: %s" % f.filename)
    return n


@route("/contracts/<int:contract_id>/edit", methods=["POST"])
@auth.login_required
def contract_edit(contract_id):
    rows = db.query("SELECT * FROM [Contracts] WHERE [ContractID] = ?", (contract_id,))
    if not rows:
        flash("لم يتم العثور على العقد.")
        return redirect(url_for("subcontractors_list"))
    sub_id = rows[0]["SubcontractorID"]
    contract_no = clean_spaces(request.form.get("contract_no", ""))
    status = request.form.get("status", "ساري")
    try:
        value, retention, advance = _float("contract_value", "قيمة العقد"), _float("retention_pct", "نسبة الاستقطاع", 100), _float("advance_pct", "نسبة الدفعة المقدمة", 100)
    except ValueError:
        flash("القيم يجب أن تكون أرقامًا.")
        return _back(sub_id, contract=contract_id, mode="edit")
    files = _files_of("contract_file") + _files_of("extra_files")
    if status not in CONTRACT_STATUSES:
        flash("حالة غير صحيحة.")
    elif contract_no and any(c["ContractID"] != contract_id and norm_key(c.get("ContractNo") or "") == norm_key(contract_no)
                             for c in db.query("SELECT [ContractID], [ContractNo] FROM [Contracts]")):
        flash("رقم العقد «%s» مستخدم لعقد آخر." % contract_no)
    elif _bad_file(files):
        flash("صيغة الملف غير مدعومة: %s" % _bad_file(files))
    else:
        db.execute("UPDATE [Contracts] SET [ContractNo] = ?, [Status] = ?, [ContractValue] = ?, [RetentionPct] = ?, [AdvancePct] = ?, "
                   "[Notes] = ? WHERE [ContractID] = ?",
                   (contract_no or None, status, value, retention, advance, request.form.get("notes", "").strip(), contract_id))
        saved = _store_contract_files(contract_id, _files_of("contract_file"), "العقد الموقّع") + _store_contract_files(contract_id, _files_of("extra_files"), "مرفق إضافي")
        flash("تم حفظ بيانات العقد%s." % (" ورُفع %d ملف" % saved if saved else ""))
    return _back(sub_id)


@route("/contracts/<int:contract_id>/delete", methods=["POST"])
@auth.login_required
def contract_delete(contract_id):
    """A contract with certificates is never deleted (its history is the money). Otherwise its record goes, and its
    folder moves to the trash area - nothing in the archive is ever destroyed."""
    rows = db.query("SELECT * FROM [Contracts] WHERE [ContractID] = ?", (contract_id,))
    if not rows:
        flash("لم يتم العثور على العقد.")
        return redirect(url_for("subcontractors_list"))
    c = rows[0]
    certs = db.query("SELECT [CertificateID] FROM [Certificates] WHERE [ContractID] = ?", (contract_id,))
    if certs:
        flash("لا يُحذف عقد له %d مستخلص. احذف مستخلصاته أولًا إن كانت غير مصروفة، أو غيّر حالته إلى «منتهي»." % len(certs))
        return _back(c["SubcontractorID"])
    folder = c.get("ArchiveFolder")
    with db.transaction() as tx:
        tx.execute("DELETE FROM [ContractSites] WHERE [ContractID] = ?", (contract_id,))
        tx.execute("DELETE FROM [Contracts] WHERE [ContractID] = ?", (contract_id,))
    trashed = archive.trash_folder(folder) if folder else None
    archive.forget_documents("CONTRACT", contract_id)
    flash("حُذف العقد%s." % (" ونُقلت ملفاته إلى سلة المحذوفات في الأرشيف" if trashed else ""))
    return _back(c["SubcontractorID"])


@route("/contracts/<int:contract_id>/sites", methods=["POST"])
@auth.login_required
def contract_site_add(contract_id):
    rows = db.query("SELECT [ContractID], [Project], [ProjectID] FROM [Contracts] WHERE [ContractID] = ?", (contract_id,))
    if not rows:
        flash("لم يتم العثور على العقد.")
        return redirect(url_for("contracts_list"))
    site_name = clean_spaces(request.form.get("site_name", ""))
    if not site_name:
        flash("اسم الموقع مطلوب.")
    elif any(norm_key(s["SiteName"]) == norm_key(site_name) for s in db.query("SELECT [SiteName] FROM [ContractSites] WHERE [ContractID] = ?", (contract_id,))):
        flash("هذا الموقع مُضاف بالفعل لهذا العقد.")
    else:
        with db.transaction() as tx:
            proj_id = rows[0].get("ProjectID") or masterdata.resolve_project(rows[0]["Project"], tx)[0]
            site_id, site_name, _new = masterdata.resolve_site(proj_id, site_name, tx)
            tx.execute("INSERT INTO [ContractSites] ([ContractID], [SiteName], [CreatedAt], [SiteRefID]) VALUES (?, ?, ?, ?)", (contract_id, site_name, datetime.now(), site_id))
        flash("تمت إضافة الموقع.")
    return redirect(url_for("contract_detail", contract_id=contract_id))


@route("/contracts/<int:contract_id>")
@auth.login_required
def contract_detail(contract_id):
    subs, contracts, certs, _p = cs.load()
    contract = contracts.get(contract_id)
    if not contract:
        flash("لم يتم العثور على العقد.")
        return redirect(url_for("contracts_list"))
    is_supplier = (contract.get("PartyKind") or "مقاول") == "مورد"
    if is_supplier:
        row = db.query("SELECT * FROM [Suppliers] WHERE [SupplierID] = ?", (contract.get("SupplierID"),))
        sub = dict(row[0], SupplierID=row[0]["SupplierID"]) if row else {}
    else:
        sub = subs.get(contract.get("SubcontractorID")) or {}
    sites = [s["SiteName"] for s in db.query("SELECT [SiteName] FROM [ContractSites] WHERE [ContractID] = ?", (contract_id,))]
    if contract.get("SiteName") and contract["SiteName"] not in sites:
        sites.insert(0, contract["SiteName"])
    fig = cs.contract_figures(contract, certs)
    gen = None
    if contract.get("Origin") == "مولَّد":
        import contract_gen
        docs = archive.list_documents("CONTRACT", contract_id, current_only=False)
        cur = lambda t: next((d for d in docs if d["DocType"] == t and d.get("IsCurrent")), None)
        v = db.query("SELECT * FROM [ContractTemplateVersions] WHERE [VersionID] = ?", (contract.get("TemplateVersionID") or 0,))
        t = db.query("SELECT * FROM [ContractTemplates] WHERE [TemplateID] = ?", (v[0]["TemplateID"],)) if v else []
        data = {d["FieldKey"]: d["FieldValue"] for d in db.query("SELECT * FROM [ContractData] WHERE [ContractID] = ?", (contract_id,))}
        gen = {"template": t[0] if t else None, "version": v[0] if v else None, "docx": cur("عقد DOCX"), "pdf": cur("عقد PDF"), "signed": cur("العقد الموقّع"), "old": [d for d in docs if d["DocType"] in ("عقد DOCX", "عقد PDF") and not d.get("IsCurrent")],
               "missing": [m for m in (data.get("_missing") or "").split("، ") if m], "history": sorted(db.query("SELECT * FROM [ContractHistory] WHERE [ContractID] = ?", (contract_id,)), key=lambda h: h["At"] or datetime.min, reverse=True),
               "gaps": contract_gen.missing_fields(contract_id)}
    return render_template("contract_detail.html", contract=contract, sub=sub, sites=sites, fig=fig, is_supplier=is_supplier, gen=gen,
                           statuses=(CONTRACT_STATUSES if contract.get("Origin") == "مولَّد" else ARCHIVED_STATUSES), files=[d for d in archive.list_documents("CONTRACT", contract_id) if d.get("IsCurrent")])


@route("/contracts/<int:contract_id>/file", methods=["POST"])
@auth.login_required
def contract_file_upload(contract_id):
    if not db.query("SELECT [ContractID] FROM [Contracts] WHERE [ContractID] = ?", (contract_id,)):
        flash("لم يتم العثور على العقد.")
        return redirect(url_for("subcontractors_list"))
    files = _files_of("contract_file")
    if not files:
        flash("اختر ملفًا أولًا.")
    elif _bad_file(files):
        flash("صيغة الملف غير مدعومة: %s" % _bad_file(files))
    elif db.query("SELECT [ContractID] FROM [Contracts] WHERE [ContractID] = ? AND [Origin] = ?", (contract_id, "مولَّد")):
        import contract_gen
        contract_gen.upload_signed(contract_id, files[0], (auth.current_user() or {}).get("Username", ""))
        flash("رُفعت النسخة الموقّعة إلى نفس سجل العقد.")
    else:
        _store_contract_files(contract_id, files, "العقد الموقّع")
        flash("تم رفع نسخة العقد إلى «ملفات العقد» داخل مجلد العقد.")
    return redirect(url_for("contract_detail", contract_id=contract_id))


@route("/contracts/<int:contract_id>/file")
@auth.login_required
def contract_file_view(contract_id):
    rows = db.query("SELECT [ContractFile] AS p FROM [Contracts] WHERE [ContractID] = ?", (contract_id,))
    if not rows or not rows[0]["p"]:
        abort(404)
    return archive.send(rows[0]["p"])

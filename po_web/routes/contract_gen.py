# -*- coding: utf-8 -*-
"""Contracts made by the system from Word templates: the wizard, the files, the signed copy, the templates screens, the company profile."""
import logging
from datetime import datetime

from flask import abort, current_app, flash, g, redirect, render_template, request, send_file, url_for

import archive
import auth
import contract_gen as cg
import contract_render
import contract_templates as ct
import contract_vars as V
import db
from routes import route
from routes.subcontractors import project_site_lists
from textnorm import clean_spaces

log = logging.getLogger("po.error")


def _user():
    return (auth.current_user() or {}).get("Username", "")


def _d(x):
    return x.strftime("%Y-%m-%d") if x else ""


def _party_options():
    subs = [("sub:%d" % s["SubcontractorID"], s["Name"]) for s in sorted(db.query("SELECT [SubcontractorID], [Name] FROM [Subcontractors]"), key=lambda s: s["Name"])]
    sups = [("sup:%d" % s["SupplierID"], s["Name"]) for s in sorted(db.query("SELECT [SupplierID], [Name] FROM [Suppliers]"), key=lambda s: s["Name"])]
    return subs, sups


# --------------------------------------------------------------------------- the wizard
def _values(cid, form):
    """What the wizard shows: the posted form (after an error), else the saved contract, else the defaults."""
    if form is not None:
        rows = lambda *n: [list(r) for r in cg._rows(form, *n)]
        lines = [r for r in rows("line_desc", "line_unit", "line_qty", "line_price") if any(x.strip() for x in r)]
        specs = [r for r in rows("spec_name", "spec_desc") if any(x.strip() for x in r)]
        trs = [r for r in rows("tr_label", "tr_pct", "tr_amount", "tr_due") if any(x.strip() for x in r)]
        v = {k: form.get(k, "") for k in ("project", "site", "contract_date", "quote_no", "quote_date", "scope", "price_model", "duration_days", "start_date", "end_date", "penalty_per_day", "penalty_max_pct", "warranty", "notes",
                                           "source_po", "source_compare")}
        v.update({"incl": form.get("prices_incl_vat") == "1", "lines": lines or [["", "", "", ""]], "specs": specs, "trs": trs, "data": {k[3:]: val for k, val in form.items() if k.startswith("cf_")}})
        return v
    if cid:
        s = cg.form_state(cid)
        c = s["c"]
        g = lambda x: "" if x is None else x
        return {"project": g(c.get("Project")), "site": g(c.get("SiteName")), "contract_date": _d(c.get("ContractDate")), "quote_no": g(c.get("QuoteNo")), "quote_date": _d(c.get("QuoteDate")), "scope": g(c.get("Scope")),
                "price_model": c.get("PriceModel") or "مقطوعية", "duration_days": g(c.get("DurationDays")), "start_date": _d(c.get("StartDate")), "end_date": _d(c.get("EndDate")), "penalty_per_day": g(c.get("PenaltyPerDay")),
                "penalty_max_pct": g(c.get("PenaltyMaxPct")), "warranty": g(c.get("WarrantyText")), "notes": g(c.get("Notes")), "source_po": g(c.get("SourcePO")), "source_compare": g(c.get("SourceCompareID")), "incl": False,
                "lines": [[l["Description"] or "", l.get("Unit") or "", g(l.get("Qty")), g(l.get("UnitPrice"))] for l in s["price_lines"]],
                "specs": [[l["Description"] or "", l.get("Note") or ""] for l in s["spec_lines"]],
                "trs": [[t.get("Label") or "", g(t.get("Pct")), g(t.get("Amount")), t.get("DueText") or ""] for t in s["tranches"]], "data": {k: v for k, v in s["data"].items() if not k.startswith("_")}}
    return {"project": request.args.get("project", ""), "site": request.args.get("site", ""), "contract_date": _d(datetime.now()), "quote_no": "", "quote_date": "", "scope": request.args.get("scope", ""), "price_model": "مقطوعية",
            "duration_days": "", "start_date": "", "end_date": "", "penalty_per_day": "", "penalty_max_pct": "", "warranty": "", "notes": "", "source_po": request.args.get("source_po", ""),
            "source_compare": request.args.get("source_compare", ""), "incl": False, "lines": [["", "", "", ""]], "specs": [], "trs": [], "data": {}}


def _wizard(cid):
    existing = db.query("SELECT * FROM [Contracts] WHERE [ContractID] = ?", (cid,)) if cid else None
    if cid and (not existing or existing[0].get("Origin") != "مولَّد"):
        flash("هذا العقد مؤرشف (وُقّع خارج النظام) ولا يُعدَّل بهذه الشاشة.")
        return redirect(url_for("contract_detail", contract_id=cid) if existing else url_for("contracts_list"))
    form = request.form if request.method == "POST" else None
    if request.method == "POST" and not form.get("_reload"):
        try:
            new_id = cg.save(form, _user(), cid)
        except cg.ContractError as e:
            flash(str(e))
        else:
            if form.get("action") == "generate":
                return _generate_and_go(new_id)
            flash("حُفظت المسودة برقم %s. راجع الحقول الناقصة ثم أنشئ الملفات." % cg.load(new_id)["c"]["ContractNo"])
            return redirect(url_for("contract_detail", contract_id=new_id))
    src = form if form is not None else request.args
    party = src.get("party") or (cg.form_state(cid)["party"] if cid else "")
    kind, pid = cg.parse_party(party)
    tpls = [t for t in ct.list_templates(active_only=True) if t["version"] and (not kind or t["PartyKind"] == kind)]
    tid = src.get("template_id") or (str(ct.db.query("SELECT [TemplateID] FROM [ContractTemplateVersions] WHERE [VersionID] = ?", (existing[0].get("TemplateVersionID") or 0,))[0]["TemplateID"])
                                     if existing and existing[0].get("TemplateVersionID") and ct.db.query("SELECT [TemplateID] FROM [ContractTemplateVersions] WHERE [VersionID] = ?", (existing[0]["TemplateVersionID"],)) else "")
    if not tid and len(tpls) == 1 and kind:
        tid = str(tpls[0]["TemplateID"])
    tpl = ct.get(int(tid)) if tid.isdigit() else None
    version = ct.current_version(tpl["TemplateID"]) if tpl else None
    custom = [f for f in ct.fields_of(version).get("fields", []) if not f["standard"]] if version else []
    gaps = cg.party_gaps(kind, pid, version) if (kind and version) else []
    projects, sites_by_project = project_site_lists()
    subs, sups = _party_options()
    return render_template("contract_gen_form.html", cid=cid, contract=(existing[0] if existing else None), party=party, kind=kind, pid=pid, tpls=tpls, tpl=tpl, version=version, custom=custom, gaps=gaps,
                           comp_gaps=cg.company_gaps(version) if version else [], v=_values(cid, form), subs=subs, sups=sups, projects=projects, sites_by_project=sites_by_project, models=cg.PRICE_MODELS,
                           ready=bool(kind and tpl and version))


def _generate_and_go(cid):
    try:
        r = cg.generate(cid, _user())
    except (cg.ContractError, ct.TemplateError, contract_render.MissingDocx) as e:
        flash(str(e))
        return redirect(url_for("contract_detail", contract_id=cid))
    except Exception as e:                                 # noqa
        log.exception("contract generation failed")
        flash("تعذّر إنشاء ملفات العقد: %s" % " ".join(str(e).split())[:200])
        return redirect(url_for("contract_detail", contract_id=cid))
    msg = "أُنشئ العقد (مراجعة %d): ملف Word%s." % (r["revision"], " وملف PDF" if r["pdf"] else "")
    if r["pdf_error"]:
        msg += " لم يُنشأ PDF تلقائيًا (%s)؛ افتح ملف Word واحفظه PDF، أو ارفع PDF يدويًا." % r["pdf_error"][:140]
    if r["missing"]:
        msg += " بقيت %d حقول ناقصة بالأصفر: %s." % (len(r["missing"]), "، ".join(k.replace("_", " ") for k in r["missing"][:6]) + (" …" if len(r["missing"]) > 6 else ""))
    flash(msg)
    return redirect(url_for("contract_detail", contract_id=cid))


@route("/contracts/generate", methods=["GET", "POST"])
@auth.login_required
def contract_generate():
    return _wizard(None)


@route("/contracts/<int:contract_id>/generate", methods=["GET", "POST"])
@auth.login_required
def contract_generate_edit(contract_id):
    return _wizard(contract_id)


@route("/contracts/<int:contract_id>/files/generate", methods=["POST"])
@auth.login_required
def contract_files_generate(contract_id):
    return _generate_and_go(contract_id)


@route("/contracts/<int:contract_id>/signed", methods=["POST"])
@auth.login_required
def contract_signed_upload(contract_id):
    f = request.files.get("signed_file")
    if not f or not f.filename:
        flash("اختر ملف النسخة الموقّعة.")
    else:
        try:
            cg.upload_signed(contract_id, f, _user())
            flash("رُفعت النسخة الموقّعة إلى نفس سجل العقد.")
        except (cg.ContractError, archive.UnsupportedFile) as e:
            flash(str(e) if isinstance(e, cg.ContractError) else "صيغة الملف غير مدعومة.")
    return redirect(url_for("contract_detail", contract_id=contract_id))


# --------------------------------------------------------------------------- the templates screens
@route("/contract-templates")
@auth.login_required
def contract_templates():
    return render_template("contract_templates.html", rows=ct.list_templates(), starters_pending=_starters_pending())


def _starters_pending():
    import json
    import os
    try:
        with open(os.path.join(ct.STARTERS_DIR, "starters.json"), encoding="utf-8") as f:
            have = {(t["Code"] or "").upper() for t in db.query("SELECT [Code] FROM [ContractTemplates]")}
            return [s["name"] for s in json.load(f) if s["code"].upper() not in have]
    except (OSError, ValueError):
        return []


@route("/contract-templates/starters", methods=["POST"])
@auth.login_required
def contract_templates_starters():
    try:
        done = ct.install_starters(_user())
    except contract_render.MissingDocx as e:
        flash(str(e))
        return redirect(url_for("contract_templates"))
    flash("أُضيفت القوالب: %s" % "، ".join(done) if done else "القوالب الأساسية موجودة بالفعل.")
    return redirect(url_for("contract_templates"))


@route("/contract-templates/guide")
@auth.login_required
def contract_templates_guide():
    groups = {}
    for k, (label, group) in V.STANDARD.items():
        groups.setdefault(group, []).append((k, label))
    return render_template("contract_templates_guide.html", groups=groups, blocks=V.BLOCKS)


@route("/contract-templates/new", methods=["GET", "POST"])
@auth.login_required
def contract_template_new():
    if request.method == "POST":
        f = request.files.get("template_file")
        if not f or not f.filename:
            flash("ارفع ملف Word للقالب.")
        else:
            try:
                tid = ct.register(request.form.get("name"), request.form.get("code"), request.form.get("contract_type"), request.form.get("party_kind"), request.form.get("prefix"), request.form.get("description"), _user())
                try:
                    ct.add_version(tid, f.filename, f.read(), "النسخة الأولى", _user())
                except ct.TemplateError:
                    db.execute("DELETE FROM [ContractTemplates] WHERE [TemplateID] = ?", (tid,))
                    raise
                flash("أُضيف القالب. راجع العلامات المكتشفة بالأسفل.")
                return redirect(url_for("contract_template_detail", template_id=tid))
            except (ct.TemplateError, contract_render.MissingDocx) as e:
                flash(str(e))
    return render_template("contract_template_new.html", form=request.form)


@route("/contract-templates/<int:template_id>")
@auth.login_required
def contract_template_detail(template_id):
    t = ct.get(template_id)
    if not t:
        flash("القالب غير موجود.")
        return redirect(url_for("contract_templates"))
    vs = ct.versions(template_id)
    cur = ct.current_version(template_id)
    used = {r["TemplateVersionID"]: r["n"] for r in [{"TemplateVersionID": x["TemplateVersionID"], "n": 0} for x in db.query("SELECT [TemplateVersionID] FROM [Contracts] WHERE [TemplateVersionID] IS NOT NULL")]}
    counts = {}
    for x in db.query("SELECT [TemplateVersionID] FROM [Contracts] WHERE [TemplateVersionID] IS NOT NULL"):
        counts[x["TemplateVersionID"]] = counts.get(x["TemplateVersionID"], 0) + 1
    return render_template("contract_template_detail.html", t=t, versions=vs, cur=cur, info=ct.fields_of(cur), counts=counts)


@route("/contract-templates/<int:template_id>/update", methods=["POST"])
@auth.login_required
def contract_template_update(template_id):
    try:
        f = request.form
        ct.update(template_id, f.get("name"), f.get("contract_type"), f.get("party_kind"), f.get("prefix"), f.get("description"), f.get("active") == "1")
        flash("حُفظت بيانات القالب.")
    except ct.TemplateError as e:
        flash(str(e))
    return redirect(url_for("contract_template_detail", template_id=template_id))


@route("/contract-templates/<int:template_id>/version", methods=["POST"])
@auth.login_required
def contract_template_version(template_id):
    f = request.files.get("template_file")
    if not f or not f.filename:
        flash("ارفع ملف Word.")
    else:
        try:
            _vid, info = ct.add_version(template_id, f.filename, f.read(), request.form.get("note", ""), _user())
            flash("أُضيفت نسخة جديدة وأصبحت هي الحالية (%d علامة، %d جدول). العقود الصادرة قبل التوقيع تستخدمها عند إعادة الإنشاء." % (len(info["fields"]), len(info["blocks"])))
        except (ct.TemplateError, contract_render.MissingDocx) as e:
            flash(str(e))
    return redirect(url_for("contract_template_detail", template_id=template_id))


@route("/contract-templates/versions/<int:version_id>/download")
@auth.login_required
def contract_template_download(version_id):
    rows = db.query("SELECT * FROM [ContractTemplateVersions] WHERE [VersionID] = ?", (version_id,))
    if not rows:
        abort(404)
    return archive.send(rows[0]["StoredPath"], as_attachment=True, download_name=rows[0]["FileName"])


@route("/contract-templates/versions/<int:version_id>/test.docx")
@auth.login_required
def contract_template_test(version_id):
    import io
    rows = db.query("SELECT * FROM [ContractTemplateVersions] WHERE [VersionID] = ?", (version_id,))
    if not rows:
        abort(404)
    try:
        data = cg.sample_render(rows[0])
    except contract_render.MissingDocx as e:
        flash(str(e))
        return redirect(url_for("contract_template_detail", template_id=rows[0]["TemplateID"]))
    except Exception as e:                                 # noqa
        flash("تعذّرت تجربة القالب: %s" % " ".join(str(e).split())[:200])
        return redirect(url_for("contract_template_detail", template_id=rows[0]["TemplateID"]))
    return send_file(io.BytesIO(data), as_attachment=True, download_name="تجربة_القالب.docx", mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


# --------------------------------------------------------------------------- the company (first party) profile
@route("/settings/company", methods=["GET", "POST"])
@auth.login_required
def settings_company():
    if request.method == "POST":
        f = request.form
        cols = ("LegalName", "CR", "VatNo", "UnifiedNo", "Address", "City", "Phone", "Email", "RepName", "RepTitle", "BankName", "IBAN", "AccountHolder")
        vals = [clean_spaces(f.get(c.lower(), "")) for c in cols]
        if not vals[0]:
            flash("اسم الشركة النظامي مطلوب.")
        else:
            if db.query("SELECT [ProfileID] FROM [CompanyProfile] WHERE [ProfileID] = 1"):
                db.execute("UPDATE [CompanyProfile] SET %s, [UpdatedAt] = ? WHERE [ProfileID] = 1" % ", ".join("[%s] = ?" % c for c in cols), tuple(vals) + (datetime.now(),))
            else:
                db.execute("INSERT INTO [CompanyProfile] ([ProfileID], %s, [UpdatedAt]) VALUES (1, %s, ?)" % (", ".join("[%s]" % c for c in cols), ", ".join("?" for _ in cols)), tuple(vals) + (datetime.now(),))
            flash("حُفظت بيانات الشركة (الطرف الأول في العقود).")
            return redirect(url_for("settings_company"))
    return render_template("settings_company.html", c=cg.company())

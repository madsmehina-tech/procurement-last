# -*- coding: utf-8 -*-
"""Management reports: the subcontractors dashboard, the project analysis and the contractor status report.
All numbers come from reporting_service (the same movements as the contractors' account statements)."""
import io
from datetime import datetime

from flask import flash, redirect, render_template, request, send_file, url_for

import auth
import cycle_files
import report_xlsx
import reporting_service as rs
from routes import route
from routes.subcontractors import project_site_lists


def _day(raw):
    try:
        return datetime.strptime((raw or "").strip(), "%Y-%m-%d")
    except ValueError:
        return None


def _scope(project_required=False):
    a = request.args
    sub = a.get("sub", type=int)
    return rs.Scope(project=a.get("project", ""), site=a.get("site", ""), sub_id=sub, date_from=_day(a.get("date_from")), date_to=_day(a.get("date_to")))


def _qs():
    return {k: v for k, v in request.args.items() if v}


def _filters(scope):
    return {"project": scope.project, "site": scope.site, "sub": scope.sub_id or "", "date_from": request.args.get("date_from", ""), "date_to": request.args.get("date_to", ""),
            "any": bool(scope.project or scope.site or scope.sub_id or scope.date_from or scope.date_to), "label": scope.label}


def _pdf(html, name):
    try:
        return send_file(io.BytesIO(cycle_files._pdf_bytes(html)), as_attachment=True, download_name=name, mimetype="application/pdf")
    except cycle_files.pdfgen.PdfError as e:
        flash("تعذّر إنشاء PDF تلقائيًا (%s) - استخدم «طباعة» ثم «حفظ كـ PDF» من المتصفح." % e)
        return None


# --------------------------------------------------------------------------- the subcontractors dashboard
def _dash_charts(d):
    subs = d["subs"]
    ratio_paid, ratio_rem = d["kpi"]["paid"], max(d["kpi"]["remaining"], 0)
    proj = [dict(label=r["name"], value=r["dues"], color="var(--p%d)" % (i % 6)) for i, r in enumerate(d["projects"][:6]) if r["dues"] > 0]
    other = sum(r["dues"] for r in d["projects"][6:])
    if other > 0:
        proj.append({"label": "مشاريع أخرى", "value": round(other, 2), "color": "var(--p5)"})
    m = d["monthly"]
    return {"top_dues": [{"label": r["name"], "value": r["dues"]} for r in sorted(subs, key=lambda r: -r["dues"])[:10] if r["dues"] > 0],
            "top_paid": [{"label": r["name"], "value": r["paid"]} for r in sorted(subs, key=lambda r: -r["paid"])[:10] if r["paid"] > 0],
            "month_labels": m["labels"], "cert_labels": ["%s (%d)" % (lab, n) for lab, n in zip(m["labels"], m["cert_count"])],
            "proj_dues": proj, "proj_dues_center": str(len(d["projects"])) if d["projects"] else "",
            "proj_certs": [{"label": r["name"], "value": r["certs"]} for r in sorted(d["projects"], key=lambda r: -r["certs"])[:10] if r["certs"] > 0],
            "ratio": [{"label": "المصروف", "value": ratio_paid, "color": "var(--p2)"}, {"label": "المتبقي", "value": ratio_rem, "color": "var(--c-rust)"}],
            "ratio_center": ("%.0f%%" % (ratio_paid / (ratio_paid + ratio_rem) * 100)) if (ratio_paid + ratio_rem) > 0 else "",
            "compare": sorted(subs, key=lambda r: -r["dues"])[:12]}


def _dashboard_data():
    scope = _scope()
    ctx = rs.Context()
    d = rs.dashboard(ctx, scope)
    return ctx, scope, d, _dash_charts(d)


@route("/subcontractors/dashboard")
@auth.login_required
def subs_dashboard():
    ctx, scope, d, charts = _dashboard_data()
    projects, sites_by_project = project_site_lists()
    sites = sorted({s for v in sites_by_project.values() for s in v}) if not scope.project else sites_by_project.get(scope.project, [])
    return render_template("subs_dashboard.html", d=d, charts=charts, f=_filters(scope), qs=_qs(), projects=projects, sites=sites, sites_by_project=sites_by_project,
                           subs=sorted(ctx.subs.values(), key=lambda s: s["Name"]))


def _dash_print_html():
    ctx, scope, d, charts = _dashboard_data()
    return render_template("subs_dashboard_print.html", d=d, charts=charts, f=_filters(scope), now=datetime.now().strftime("%Y-%m-%d %H:%M"))


@route("/subcontractors/dashboard/print")
@auth.login_required
def subs_dashboard_print():
    return _dash_print_html()


@route("/subcontractors/dashboard/export.pdf")
@auth.login_required
def subs_dashboard_pdf():
    resp = _pdf(_dash_print_html(), "subcontractors_dashboard_%s.pdf" % datetime.now().strftime("%Y%m%d"))
    return resp or redirect(url_for("subs_dashboard", **_qs()))


@route("/subcontractors/dashboard/export.xlsx")
@auth.login_required
def subs_dashboard_xlsx():
    _ctx, scope, d, _charts = _dashboard_data()
    k, m = d["kpi"], d["monthly"]
    kpis = [("إجمالي قيمة العقود", k["contracts_value"]), ("إجمالي المستخلصات", k["certs_total"]), ("إجمالي المصروف", k["paid"]), ("إجمالي المتبقي", k["remaining"]),
            ("إجمالي المستحقات الحالية", k["current_dues"]), ("إجمالي المستحقات (مدين)", k["dues"]), ("نسبة المصروف %", k["paid_ratio"]), ("عدد المقاولين", k["contractors"]),
            ("عدد العقود النشطة", k["active_contracts"]), ("عدد المستخلصات المفتوحة", k["open_certs"]), ("عدد المستخلصات المصروفة", k["paid_certs"]),
            ("عدد المستخلصات المؤجلة", k["deferred_certs"]), ("عدد المستخلصات داخل دورات الصرف الحالية", k["in_cycle_certs"])]
    buf = report_xlsx.build([
        {"title": "المؤشرات", "band": "لوحة مؤشرات مقاولي الباطن", "headers": ["المؤشر", "القيمة"], "rows": [list(x) for x in kpis], "widths": (46, 20), "money": (2,), "table": "tblKpi"},
        {"title": "المقاولون", "band": "المستحقات والمصروف والمتبقي لكل مقاول", "headers": ["المقاول", "المستحقات", "المصروف", "المتبقي", "نسبة المصروف %"],
         "rows": [[r["name"], r["dues"], r["paid"], r["remaining"], round(r["paid"] / r["dues"] * 100, 1) if r["dues"] else 0] for r in d["subs"]], "widths": (40, 18, 18, 18, 16), "money": (2, 3, 4), "table": "tblContractors"},
        {"title": "المشاريع", "band": "توزيع المستحقات والمستخلصات حسب المشروع", "headers": ["المشروع", "المستحقات", "المصروف", "عدد المستخلصات"],
         "rows": [[r["name"], r["dues"], r["paid"], r["certs"]] for r in d["projects"]], "widths": (40, 18, 18, 16), "money": (2, 3), "table": "tblProjects"},
        {"title": "شهري", "band": "تطور الصرف والمستخلصات شهريًا", "headers": ["الشهر", "المصروف", "عدد المستخلصات", "قيمة المستخلصات"],
         "rows": [[m["labels"][i], m["paid"][i], m["cert_count"][i], m["cert_value"][i]] for i in range(len(m["labels"]))], "widths": (22, 18, 16, 18), "money": (2, 4), "table": "tblMonthly"}], scope.label)
    return send_file(buf, as_attachment=True, download_name="subcontractors_dashboard_%s.xlsx" % datetime.now().strftime("%Y%m%d"), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# --------------------------------------------------------------------------- the project analysis
def _project_data():
    scope = _scope()
    ctx = rs.Context()
    names = rs.projects(ctx)
    if not scope.project and names:
        scope = rs.Scope(project=names[0], date_from=scope.date_from, date_to=scope.date_to)
    return ctx, names, scope, (rs.project_analysis(ctx, scope.project, scope.date_from, scope.date_to) if scope.project else None)


def _project_charts(a):
    m = a["monthly"]
    return {"cost": [dict(p, color="var(--p%d)" % i) for i, p in enumerate(a["cost"])], "months": m["labels"]}


@route("/reports/projects")
@auth.login_required
def project_analysis():
    ctx, names, scope, a = _project_data()
    return render_template("project_analysis.html", a=a, charts=_project_charts(a) if a else None, projects=names, f=_filters(scope), qs=dict(_qs(), project=scope.project))


def _project_print_html():
    ctx, names, scope, a = _project_data()
    return render_template("project_analysis_print.html", a=a, charts=_project_charts(a) if a else None, f=_filters(scope), now=datetime.now().strftime("%Y-%m-%d %H:%M"), project=scope.project)


@route("/reports/projects/print")
@auth.login_required
def project_analysis_print():
    return _project_print_html()


@route("/reports/projects/export.pdf")
@auth.login_required
def project_analysis_pdf():
    resp = _pdf(_project_print_html(), "project_analysis_%s.pdf" % datetime.now().strftime("%Y%m%d"))
    return resp or redirect(url_for("project_analysis", **_qs()))


@route("/reports/projects/export.xlsx")
@auth.login_required
def project_analysis_xlsx():
    _ctx, _names, scope, a = _project_data()
    if not a:
        flash("اختر مشروعًا أولًا.")
        return redirect(url_for("project_analysis"))
    k, m = a["kpi"], a["monthly"]
    summary = [("إجمالي قيمة أوامر الشراء", k["po_total"]), ("إجمالي قيمة العقود", k["contracts_value"]), ("إجمالي قيمة المستخلصات", k["certs_total"]), ("مصروف المقاولين", k["sub_paid"]),
               ("مصروف المشتريات (أوامر مسددة)", k["po_paid"]), ("إجمالي المصروف", k["paid_total"]), ("متبقي المقاولين", k["sub_remaining"]), ("متبقي المشتريات (غير مسدد)", k["po_remaining"]),
               ("إجمالي المتبقي", k["remaining_total"]), ("عدد الموردين", k["suppliers"]), ("عدد المقاولين", k["contractors"]), ("عدد العقود", k["contracts"]),
               ("عدد المستخلصات", k["certs"]), ("عدد أوامر الشراء", k["orders"])]
    day = lambda d: d.strftime("%Y-%m-%d") if d else ""
    buf = report_xlsx.build([
        {"title": "الملخص", "band": "تحليل المشروع: " + scope.project, "headers": ["المؤشر", "القيمة"], "rows": [list(x) for x in summary], "widths": (42, 20), "money": (2,), "table": "tblSummary"},
        {"title": "أوامر الشراء", "band": "أوامر شراء " + scope.project, "headers": ["رقم الأمر", "التاريخ", "المورد", "الموقع", "الإجمالي", "مسدد"],
         "rows": [[r["po"], day(r["date"]), r["supplier"], r["site"], r["total"], "نعم" if r["paid"] else "لا"] for r in a["po_rows"]], "widths": (18, 14, 36, 24, 18, 10), "money": (5,), "table": "tblOrders"},
        {"title": "العقود", "band": "عقود " + scope.project, "headers": ["رقم العقد", "المقاول", "الموقع", "قيمة العقد", "الحالة"],
         "rows": [[r["no"], r["sub"], r["site"], r["value"], r["status"]] for r in a["contract_rows"]], "widths": (20, 36, 24, 18, 14), "money": (4,), "table": "tblContracts"},
        {"title": "المستخلصات", "band": "مستخلصات " + scope.project, "headers": ["رقم المستخلص", "المقاول", "الموقع", "التاريخ", "القيمة", "المصروف", "المتبقي", "الحالة"],
         "rows": [[r["no"], r["sub"], r["site"], day(r["date"]), r["total"], r["paid"], r["remaining"], r["status"]] for r in a["cert_rows"]], "widths": (14, 36, 24, 14, 16, 16, 16, 14), "money": (5, 6, 7), "table": "tblCertificates"},
        {"title": "الدفعات", "band": "دفعات " + scope.project, "headers": ["التاريخ", "المقاول", "الموقع", "رقم الدفعة", "البيان", "المبلغ"],
         "rows": [[day(r["date"]), r["sub"], r["site"], r["no"] or "", r["desc"], r["amount"]] for r in a["pay_rows"]], "widths": (14, 36, 24, 12, 50, 16), "money": (6,), "table": "tblPayments"},
        {"title": "شهري", "band": "المصروف الشهري", "headers": ["الشهر", "مصروف مقاولين", "مصروف مشتريات"],
         "rows": [[m["labels"][i], m["subs"][i], m["pos"][i]] for i in range(len(m["labels"]))], "widths": (22, 18, 18), "money": (2, 3), "table": "tblMonthly"}], scope.label or scope.project)
    return send_file(buf, as_attachment=True, download_name="project_analysis_%s.xlsx" % datetime.now().strftime("%Y%m%d"), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


# --------------------------------------------------------------------------- the contractor status report
def _contractor_html(sub_id):
    r = rs.contractor_report(rs.Context(), sub_id)
    return render_template("contractor_report.html", r=r, now=datetime.now().strftime("%Y-%m-%d %H:%M")) if r else None


@route("/subcontractors/<int:sub_id>/status-report")
@auth.login_required
def contractor_status_report(sub_id):
    html = _contractor_html(sub_id)
    if not html:
        flash("لم يتم العثور على المقاول.")
        return redirect(url_for("subcontractors_list"))
    return html


@route("/subcontractors/<int:sub_id>/status-report.pdf")
@auth.login_required
def contractor_status_pdf(sub_id):
    html = _contractor_html(sub_id)
    if not html:
        return redirect(url_for("subcontractors_list"))
    resp = _pdf(html, "contractor_status_%s_%s.pdf" % (sub_id, datetime.now().strftime("%Y%m%d")))
    return resp or redirect(url_for("contractor_status_report", sub_id=sub_id))

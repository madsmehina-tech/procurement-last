# -*- coding: utf-8 -*-
"""Purchase-orders dashboard (screen) and its Excel report."""
import io
from datetime import datetime

from flask import render_template, send_file

import analytics_service
import auth
import excel_dashboard
from order_services import filter_choices, filtered_orders, site_choices
from routes import route


@route("/orders/analytics")
@auth.login_required
def orders_analytics():
    rows, ctx = filtered_orders(analytics_service.COLUMNS)
    all_projects, all_suppliers = filter_choices()
    return render_template("orders_analytics.html", all_projects=all_projects, all_suppliers=all_suppliers, all_sites=site_choices(), **ctx,
                           **analytics_service.build(rows))


@route("/orders/analytics/export.xlsx")
@auth.login_required
def orders_analytics_export():
    rows, ctx = filtered_orders(analytics_service.COLUMNS)
    data = analytics_service.build(rows)
    filters = []
    if ctx["f_project"]:
        filters.append("المشروع: %s" % ctx["f_project"])
    if ctx["f_supplier"]:
        filters.append("المورد: %s" % ctx["f_supplier"])
    if ctx.get("f_site"):
        filters.append("الموقع: %s" % ctx["f_site"])
    if ctx["f_date_from"] or ctx["f_date_to"]:
        filters.append("الفترة: %s إلى %s" % (ctx["f_date_from"] or "البداية", ctx["f_date_to"] or "اليوم"))
    wb = excel_dashboard.build_workbook(data, rows, " | ".join(filters) or "كل البيانات")
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    fname = "purchase_orders_dashboard_%s.xlsx" % datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(buf, as_attachment=True, download_name=fname,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

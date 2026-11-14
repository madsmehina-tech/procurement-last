# -*- coding: utf-8 -*-
"""The charts + filters panel (28.0): its data for the browser and its Excel export. See insights.py."""
import io
from datetime import datetime

from flask import abort, jsonify, request, send_file

import auth
import insights
from routes import route


def _allowed(page):
    if page not in insights.PAGES:
        abort(404)
    if not auth.can(insights.PERMS[page]):
        abort(403)


@route("/insights/<page>/data.json")
@auth.login_required
def insights_data(page):
    _allowed(page)
    return jsonify({"meta": insights.meta(page), "rows": insights.rows_for(page, auth.can)})


@route("/insights/<page>/export.xlsx")
@auth.login_required
def insights_export(page):
    _allowed(page)
    rows = insights.apply(insights.rows_for(page, auth.can), request.args)
    data = insights.to_xlsx(page, rows, request.args)
    return send_file(io.BytesIO(data), as_attachment=True, download_name="PO_%s_%s.xlsx" % (page, datetime.now().strftime("%Y%m%d_%H%M")),
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

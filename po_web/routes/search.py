# -*- coding: utf-8 -*-
"""The one search box (top of every page)."""
from flask import render_template, request, url_for

import auth
import search_service
from routes import route


@route("/search")
@auth.login_required
def global_search():
    q = (request.args.get("q") or "").strip()[:80]
    return render_template("search.html", q=q, groups=search_service.search(q, url_for) if q else [])

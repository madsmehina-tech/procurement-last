# -*- coding: utf-8 -*-
"""«تقييم الموردين» (29.0): the ranking page and the weights. See supplier_rating.py."""
from flask import flash, redirect, render_template, request, url_for

import auth
import audit
import supplier_rating
from routes import route


@route("/suppliers/rating")
@auth.login_required
def supplier_rating_page():
    rows = supplier_rating.compute()
    sel = request.args.get("id", type=int)
    pick = next((r for r in rows if r["id"] == sel), rows[0] if rows else None)
    return render_template("supplier_rating.html", active="suppliers", rows=rows, pick=pick, criteria=supplier_rating.CRITERIA,
                           weights=supplier_rating.weights())


@route("/suppliers/rating/weights", methods=["POST"])
@auth.login_required
def supplier_rating_weights():
    w, err = supplier_rating.save_weights(request.form)
    if err:
        flash(err)
    else:
        audit.detail("أوزان التقييم: " + " / ".join(str(w[k]) for k, _l in supplier_rating.CRITERIA))
        flash("حُفظت أوزان التقييم.")
    return redirect(url_for("supplier_rating_page"))

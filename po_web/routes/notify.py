# -*- coding: utf-8 -*-
"""«التنبيهات» (29.0): the alerts page, «تم» (mark as seen) and the alerts settings. See alerts.py."""
from flask import flash, redirect, render_template, request, url_for

import alerts
import audit
import auth
import db
from routes import route


@route("/notifications")
@auth.login_required
def notifications_page():
    mine = alerts.for_user(auth.current_user(), auth.can, url_for)
    show_read = request.args.get("all") == "1"
    return render_template("notifications.html", active="notifications", items=[a for a in mine if show_read or not a["read"]],
                           n_unread=sum(1 for a in mine if not a["read"]), n_read=sum(1 for a in mine if a["read"]), show_read=show_read)


@route("/notifications/read", methods=["POST"])
@auth.login_required
def notifications_read():
    user = auth.current_user()
    if request.form.get("all") == "1":
        keys = [a["key"] for a in alerts.for_user(user, auth.can, url_for) if not a["read"]]
    else:
        keys = request.form.getlist("key")
    alerts.mark_read(user["UserID"], keys)
    nxt = request.form.get("next") or ""
    return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("notifications_page"))


@route("/settings/alerts", methods=["GET", "POST"])
@auth.login_required
def settings_alerts():
    if request.method == "POST":
        cfg, errors = alerts.save_settings(request.form)
        if errors:
            for e in errors:
                flash(e)
            cfg = alerts.settings()
        else:
            audit.detail("التنبيهات المفعّلة: " + "، ".join(alerts.RULES[k][0] for k, c in cfg.items() if c["on"]))
            flash("حُفظت إعدادات التنبيهات.")
            return redirect(url_for("settings_alerts"))
    else:
        cfg = alerts.settings()
    roles = db.query("SELECT [RoleKey], [RoleName] FROM [Roles] ORDER BY [RoleID]")
    users = [u for u in db.query("SELECT [UserID], [Username], [FullName], [Status] FROM [Users] ORDER BY [FullName]") if (u.get("Status") or "active") == "active"]
    return render_template("settings_alerts.html", active="settings_alerts", tab="alerts", rules=alerts.RULES, cfg=cfg, roles=roles, users=users)

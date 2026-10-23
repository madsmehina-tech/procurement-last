# -*- coding: utf-8 -*-
"""Sign in, sign out, and asking for a new account (which only the Administrator can approve)."""
import re
import time

from flask import flash, redirect, render_template, request, session, url_for
from werkzeug.security import generate_password_hash

import audit
import auth
import db
from routes import route

REG_WINDOW = 3600
REG_MAX = 5
_reg_hits = {}                       # ip -> [timestamps]   (in memory, one server process)
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,30}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _safe_next(target):
    """Only follow a 'next' address that stays on this site (no open redirect)."""
    return target if target and target.startswith("/") and not target.startswith("//") else None


@route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        key = (username.lower(), request.remote_addr)
        if auth.is_locked(key):
            audit.log("login_blocked", "مستخدم", username)
            flash("محاولات كثيرة خاطئة - تم إيقاف الدخول مؤقتًا. حاول بعد 10 دقائق.")
            return render_template("login.html")
        user = auth.verify_login(username, password)
        if not user:
            auth.register_failure(key)
            audit.log("login_failed", "مستخدم", username)
            flash("اسم المستخدم أو كلمة المرور غير صحيحة.")
            return render_template("login.html")
        status = user.get("Status") or "active"
        if status != "active":
            audit.log("login_pending", "مستخدم", username, status, user=user)
            flash({"pending": "حسابك بانتظار موافقة مدير النظام. سيتم إشعارك حين يُفعَّل.",
                   "rejected": "تم رفض طلب التسجيل. تواصل مع مدير النظام.",
                   "disabled": "هذا الحساب معطّل. تواصل مع مدير النظام."}.get(status, "الحساب غير مفعّل."))
            return render_template("login.html")
        auth.clear_failures(key)
        session.clear()
        session.permanent = True
        session["user_id"] = user["UserID"]
        db.execute("UPDATE [Users] SET [LastLoginAt] = Now() WHERE [UserID] = ?", (user["UserID"],))
        audit.log("login_ok", "مستخدم", username, user=user)
        return redirect(_safe_next(request.args.get("next")) or url_for("dashboard"))
    return render_template("login.html")


@route("/logout")
def logout():
    user = auth.current_user()
    if user:
        audit.log("logout", "مستخدم", user.get("Username"), user=user)
    session.clear()
    return redirect(url_for("login"))


@route("/register", methods=["GET", "POST"])
def register():
    form = request.form
    if request.method == "POST":
        now = time.time()
        hits = [t for t in _reg_hits.get(request.remote_addr, []) if now - t < REG_WINDOW]
        _reg_hits[request.remote_addr] = hits
        if len(hits) >= REG_MAX:
            flash("طلبات تسجيل كثيرة من هذا الجهاز. حاول لاحقًا.")
            return render_template("register.html", form=form)
        full_name = form.get("full_name", "").strip()
        username = form.get("username", "").strip()
        email = form.get("email", "").strip()
        job = form.get("job_title", "").strip()
        pw, pw2 = form.get("password", ""), form.get("password2", "")
        error = None
        if len(full_name) < 3:
            error = "اكتب الاسم الكامل."
        elif not USERNAME_RE.match(username):
            error = "اسم المستخدم من 3 إلى 30 حرفًا إنجليزيًا أو أرقامًا (ويُسمح بـ . _ -)."
        elif email and not EMAIL_RE.match(email):
            error = "البريد الإلكتروني غير صحيح."
        elif len(pw) < 8 or not re.search(r"[A-Za-z]", pw) or not re.search(r"\d", pw):
            error = "كلمة المرور 8 أحرف على الأقل وفيها حرف ورقم."
        elif pw != pw2:
            error = "كلمتا المرور غير متطابقتين."
        elif auth.find_user(username):
            error = "اسم المستخدم مستخدم بالفعل، اختر اسمًا آخر."
        if error:
            flash(error)
            return render_template("register.html", form=form)
        _reg_hits[request.remote_addr].append(now)
        uid = db.execute_insert(
            "INSERT INTO [Users] ([Username], [PasswordHash], [FullName], [Email], [JobTitle], [Status], [Role], [CreatedAt]) "
            "VALUES (?, ?, ?, ?, ?, 'pending', 'user', Now())",
            (username, generate_password_hash(pw), full_name, email, job),
        )
        audit.log("register", "مستخدم", username, job, user={"UserID": uid, "Username": username})
        flash("تم إرسال طلبك لمدير النظام. ستتمكن من الدخول بعد الموافقة عليه.")
        return redirect(url_for("login"))
    return render_template("register.html", form={})

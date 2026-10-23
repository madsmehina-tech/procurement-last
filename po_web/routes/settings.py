# -*- coding: utf-8 -*-
"""Settings: my account, users & approvals, roles & permissions, groups, audit log."""
import os
import re
from datetime import datetime, timedelta

from flask import abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import archive
import audit
import auth
import config
import db
import permissions
import projects
import sites_service
from routes import route

IMAGE_EXT = {"jpg", "jpeg", "png", "webp"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
AVATAR_MAX = 2 * 1024 * 1024


def _me():
    return auth.current_user()


def _roles():
    return sorted(db.query("SELECT [RoleID], [RoleKey], [RoleName], [Description], [IsSystem] FROM [Roles]"),
                  key=lambda r: r["RoleID"])


def _admin_role_id():
    return next((r["RoleID"] for r in _roles() if r["RoleKey"] == permissions.ADMIN_KEY), None)


def _active_admins(excluding=None):
    rid = _admin_role_id()
    return [u for u in db.query("SELECT [UserID], [Status], [RoleID] FROM [Users]")
            if u["RoleID"] == rid and (u["Status"] or "active") == "active" and u["UserID"] != excluding]


# ------------------------------------------------------------------ my account
@route("/settings/account")
def settings_account():
    return render_template("settings_account.html", me=_me(), tab="account")


@route("/settings/account/profile", methods=["POST"])
def settings_profile_save():
    name = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip()
    if len(name) < 3:
        flash("اكتب الاسم الكامل.")
    elif email and not EMAIL_RE.match(email):
        flash("البريد الإلكتروني غير صحيح.")
    else:
        db.execute("UPDATE [Users] SET [FullName] = ?, [Email] = ? WHERE [UserID] = ?", (name, email, session["user_id"]))
        flash("تم حفظ بيانات الحساب.")
    return redirect(url_for("settings_account"))


@route("/settings/account/password", methods=["POST"])
def settings_password_save():
    me = auth.find_user(_me()["Username"])
    cur, new, new2 = (request.form.get(k, "") for k in ("current", "new", "new2"))
    if not me or not check_password_hash(me["PasswordHash"], cur):
        flash("كلمة المرور الحالية غير صحيحة.")
    elif len(new) < 8 or not re.search(r"[A-Za-z]", new) or not re.search(r"\d", new):
        flash("كلمة المرور الجديدة 8 أحرف على الأقل وفيها حرف ورقم.")
    elif new != new2:
        flash("كلمتا المرور الجديدتان غير متطابقتين.")
    else:
        db.execute("UPDATE [Users] SET [PasswordHash] = ? WHERE [UserID] = ?", (generate_password_hash(new), session["user_id"]))
        flash("تم تغيير كلمة المرور.")
    return redirect(url_for("settings_account"))


@route("/settings/account/avatar", methods=["POST"])
def settings_avatar_save():
    f = request.files.get("avatar")
    if not f or not f.filename:
        flash("اختر صورة أولًا.")
        return redirect(url_for("settings_account"))
    ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    data = f.read(AVATAR_MAX + 1)
    if ext not in IMAGE_EXT or not data:
        flash("الصور المسموحة: JPG أو PNG أو WEBP.")
    elif len(data) > AVATAR_MAX:
        flash("حجم الصورة أكبر من 2 ميجابايت.")
    else:
        uid = session["user_id"]
        folder = "_النظام/المستخدمون/%s" % uid
        archive.ensure_folder(folder)
        name = "avatar_%s.%s" % (datetime.now().strftime("%Y%m%d%H%M%S"), "jpg" if ext == "jpeg" else ext)
        with open(os.path.join(archive.abs_path(folder), name), "wb") as out:
            out.write(data)
        db.execute("UPDATE [Users] SET [AvatarFile] = ? WHERE [UserID] = ?", ("%s/%s" % (folder, name), uid))
        flash("تم تغيير الصورة.")
    return redirect(url_for("settings_account"))


@route("/avatar/<int:user_id>")
def avatar_view(user_id):
    rows = db.query("SELECT [AvatarFile] FROM [Users] WHERE [UserID] = ?", (user_id,))
    if not rows or not rows[0]["AvatarFile"]:
        abort(404)
    return archive.send(rows[0]["AvatarFile"])


# ------------------------------------------------------------------ users & approvals
@route("/settings/users")
def settings_users():
    users = sorted(db.query("SELECT [UserID], [Username], [FullName], [Email], [JobTitle], [Status], [RoleID], "
                            "[CreatedAt], [LastLoginAt], [AvatarFile] FROM [Users]"), key=lambda u: u["UserID"])
    groups = {g["GroupID"]: g["GroupName"] for g in db.query("SELECT [GroupID], [GroupName] FROM [AppGroups]")}
    member = {}
    for ug in db.query("SELECT [UserID], [GroupID] FROM [AppUserGroups]"):
        member.setdefault(ug["UserID"], []).append(groups.get(ug["GroupID"], "?"))
    roles = _roles()
    names = {r["RoleID"]: r["RoleName"] for r in roles}
    pending = [u for u in users if u["Status"] == "pending"]
    others = [u for u in users if u["Status"] != "pending"]
    return render_template("settings_users.html", tab="users", pending=pending, users=others, roles=roles,
                           role_names=names, member=member, me=_me(), viewer_role=next((r["RoleID"] for r in roles if r["RoleKey"] == "viewer"), None))


def _target(user_id):
    rows = db.query("SELECT [UserID], [Username], [Status], [RoleID] FROM [Users] WHERE [UserID] = ?", (user_id,))
    if not rows:
        abort(404)
    return rows[0]


@route("/settings/users/<int:user_id>/approve", methods=["POST"])
def user_approve(user_id):
    u = _target(user_id)
    role_id = request.form.get("role_id", type=int)
    if not role_id or role_id not in {r["RoleID"] for r in _roles()}:
        flash("اختر الدور الذي سيحصل عليه المستخدم.")
        return redirect(url_for("settings_users"))
    db.execute("UPDATE [Users] SET [Status] = 'active', [RoleID] = ?, [ApprovedBy] = ?, [ApprovedAt] = Now() WHERE [UserID] = ?",
               (role_id, _me()["Username"], user_id))
    audit.log("user_approve", "مستخدم", u["Username"], "role=%s" % role_id)
    flash("تمت الموافقة على «%s» وتفعيل حسابه." % u["Username"])
    return redirect(url_for("settings_users"))


@route("/settings/users/<int:user_id>/reject", methods=["POST"])
def user_reject(user_id):
    u = _target(user_id)
    if u["Status"] == "pending":
        db.execute("UPDATE [Users] SET [Status] = 'rejected' WHERE [UserID] = ?", (user_id,))
        audit.log("user_reject", "مستخدم", u["Username"])
        flash("تم رفض طلب «%s»." % u["Username"])
    return redirect(url_for("settings_users"))


@route("/settings/users/<int:user_id>/update", methods=["POST"])
def user_update(user_id):
    u = _target(user_id)
    role_id = request.form.get("role_id", type=int)
    job = request.form.get("job_title", "").strip()
    valid = {r["RoleID"] for r in _roles()}
    if role_id not in valid:
        flash("دور غير صحيح.")
    elif role_id != u["RoleID"] and u["RoleID"] == _admin_role_id() and not _active_admins(excluding=user_id):
        flash("لا يمكن إزالة صلاحية آخر مدير نظام.")
    elif user_id == session["user_id"] and role_id != u["RoleID"]:
        flash("لا يمكنك تغيير دور حسابك بنفسك.")
    else:
        db.execute("UPDATE [Users] SET [RoleID] = ?, [JobTitle] = ? WHERE [UserID] = ?", (role_id, job, user_id))
        audit.log("user_update", "مستخدم", u["Username"], "role=%s" % role_id)
        flash("تم حفظ بيانات «%s»." % u["Username"])
    return redirect(url_for("settings_users"))


@route("/settings/users/<int:user_id>/toggle", methods=["POST"])
def user_toggle(user_id):
    u = _target(user_id)
    if user_id == session["user_id"]:
        flash("لا يمكنك تعطيل حسابك بنفسك.")
    elif u["Status"] == "active":
        if u["RoleID"] == _admin_role_id() and not _active_admins(excluding=user_id):
            flash("لا يمكن تعطيل آخر مدير نظام.")
        else:
            db.execute("UPDATE [Users] SET [Status] = 'disabled' WHERE [UserID] = ?", (user_id,))
            audit.log("user_toggle", "مستخدم", u["Username"], "disabled")
            flash("تم تعطيل «%s»." % u["Username"])
    elif u["Status"] in ("disabled", "rejected"):
        db.execute("UPDATE [Users] SET [Status] = 'active' WHERE [UserID] = ?", (user_id,))
        audit.log("user_toggle", "مستخدم", u["Username"], "active")
        flash("تم تفعيل «%s»." % u["Username"])
    return redirect(url_for("settings_users"))


@route("/settings/users/<int:user_id>/password", methods=["POST"])
def user_reset_password(user_id):
    u = _target(user_id)
    new = request.form.get("new", "")
    if len(new) < 8 or not re.search(r"[A-Za-z]", new) or not re.search(r"\d", new):
        flash("كلمة المرور الجديدة 8 أحرف على الأقل وفيها حرف ورقم.")
    else:
        db.execute("UPDATE [Users] SET [PasswordHash] = ? WHERE [UserID] = ?", (generate_password_hash(new), user_id))
        audit.log("user_reset_password", "مستخدم", u["Username"])
        flash("تمت إعادة تعيين كلمة مرور «%s». أبلغه بها." % u["Username"])
    return redirect(url_for("settings_users"))


# ------------------------------------------------------------------ roles & permissions
@route("/settings/roles")
def settings_roles():
    roles = _roles()
    granted = {}
    for rp in db.query("SELECT [RoleID], [PermCode] FROM [RolePermissions]"):
        granted.setdefault(rp["RoleID"], set()).add(rp["PermCode"])
    counts = {}
    for u in db.query("SELECT [RoleID] FROM [Users]"):
        counts[u["RoleID"]] = counts.get(u["RoleID"], 0) + 1
    return render_template("settings_roles.html", tab="roles", roles=roles, granted=granted, counts=counts,
                           catalog=permissions.CATALOG, admin_key=permissions.ADMIN_KEY)


@route("/settings/roles/save", methods=["POST"])
def roles_save():
    for r in _roles():
        if r["RoleKey"] == permissions.ADMIN_KEY:
            continue
        want = {c for c in permissions.ALL_CODES if request.form.get("p_%s_%s" % (r["RoleID"], c))}
        have = {x["PermCode"] for x in db.query("SELECT [PermCode] FROM [RolePermissions] WHERE [RoleID] = ?", (r["RoleID"],))}
        for code in have - want:
            db.execute("DELETE FROM [RolePermissions] WHERE [RoleID] = ? AND [PermCode] = ?", (r["RoleID"], code))
        for code in want - have:
            db.execute("INSERT INTO [RolePermissions] ([RoleID], [PermCode]) VALUES (?, ?)", (r["RoleID"], code))
    auth.invalidate_roles()
    audit.log("roles_save", "أدوار", "", "تعديل مصفوفة الصلاحيات")
    flash("تم حفظ الصلاحيات.")
    return redirect(url_for("settings_roles"))


@route("/settings/roles/new", methods=["POST"])
def role_create():
    name = request.form.get("name", "").strip()
    desc = request.form.get("description", "").strip()
    if len(name) < 2:
        flash("اكتب اسم الدور.")
    elif name in {r["RoleName"] for r in _roles()}:
        flash("يوجد دور بهذا الاسم.")
    else:
        key = "custom_%s" % datetime.now().strftime("%Y%m%d%H%M%S")
        db.execute_insert("INSERT INTO [Roles] ([RoleKey], [RoleName], [Description], [IsSystem]) VALUES (?, ?, ?, False)", (key, name, desc))
        audit.log("role_create", "دور", name)
        flash("تم إنشاء الدور «%s». حدّد صلاحياته من المصفوفة ثم احفظ." % name)
    return redirect(url_for("settings_roles"))


@route("/settings/roles/<int:role_id>/delete", methods=["POST"])
def role_delete(role_id):
    role = next((r for r in _roles() if r["RoleID"] == role_id), None)
    if not role:
        abort(404)
    if role["IsSystem"]:
        flash("الأدوار الأساسية لا تُحذف (يمكنك تعديل صلاحياتها فقط).")
    elif any(u["RoleID"] == role_id for u in db.query("SELECT [RoleID] FROM [Users]")):
        flash("لا يمكن حذف دور يستخدمه مستخدمون. غيّر أدوارهم أولًا.")
    else:
        db.execute("DELETE FROM [RolePermissions] WHERE [RoleID] = ?", (role_id,))
        db.execute("DELETE FROM [Roles] WHERE [RoleID] = ?", (role_id,))
        auth.invalidate_roles()
        audit.log("role_delete", "دور", role["RoleName"])
        flash("تم حذف الدور.")
    return redirect(url_for("settings_roles"))


# ------------------------------------------------------------------ groups
@route("/settings/groups")
def settings_groups():
    groups = sorted(db.query("SELECT [GroupID], [GroupName], [Description] FROM [AppGroups]"), key=lambda g: g["GroupID"])
    members = {}
    for ug in db.query("SELECT [UserID], [GroupID] FROM [AppUserGroups]"):
        members.setdefault(ug["GroupID"], set()).add(ug["UserID"])
    users = [u for u in sorted(db.query("SELECT [UserID], [FullName], [Username], [Status] FROM [Users]"), key=lambda u: u["UserID"])
             if (u["Status"] or "active") == "active"]
    return render_template("settings_groups.html", tab="groups", groups=groups, members=members, users=users)


@route("/settings/groups/new", methods=["POST"])
def group_create():
    name = request.form.get("name", "").strip()
    if len(name) < 2:
        flash("اكتب اسم المجموعة.")
    elif name in {g["GroupName"] for g in db.query("SELECT [GroupName] FROM [AppGroups]")}:
        flash("توجد مجموعة بهذا الاسم.")
    else:
        db.execute_insert("INSERT INTO [AppGroups] ([GroupName], [Description]) VALUES (?, ?)", (name, request.form.get("description", "").strip()))
        audit.log("group_create", "مجموعة", name)
        flash("تم إنشاء المجموعة «%s»." % name)
    return redirect(url_for("settings_groups"))


@route("/settings/groups/<int:group_id>/save", methods=["POST"])
def group_save(group_id):
    want = {int(x) for x in request.form.getlist("members") if x.isdigit()}
    have = {r["UserID"] for r in db.query("SELECT [UserID] FROM [AppUserGroups] WHERE [GroupID] = ?", (group_id,))}
    for uid in have - want:
        db.execute("DELETE FROM [AppUserGroups] WHERE [GroupID] = ? AND [UserID] = ?", (group_id, uid))
    for uid in want - have:
        db.execute("INSERT INTO [AppUserGroups] ([UserID], [GroupID]) VALUES (?, ?)", (uid, group_id))
    audit.log("group_save", "مجموعة", group_id, "%d عضوًا" % len(want))
    flash("تم حفظ أعضاء المجموعة.")
    return redirect(url_for("settings_groups"))


@route("/settings/groups/<int:group_id>/delete", methods=["POST"])
def group_delete(group_id):
    db.execute("DELETE FROM [AppUserGroups] WHERE [GroupID] = ?", (group_id,))
    db.execute("DELETE FROM [AppGroups] WHERE [GroupID] = ?", (group_id,))
    audit.log("group_delete", "مجموعة", group_id)
    flash("تم حذف المجموعة.")
    return redirect(url_for("settings_groups"))


# ------------------------------------------------------------------ audit log
@route("/settings/audit")
def settings_audit():
    q_user = request.args.get("user", "").strip()
    q_action = request.args.get("action", "").strip()
    d_from, d_to = request.args.get("date_from", ""), request.args.get("date_to", "")
    rows = db.query("SELECT [LogID], [LoggedAt], [Username], [ActionName], [EntityType], [EntityKey], [Details], [IPAddress] FROM [AuditLog]")
    try:
        lo = datetime.strptime(d_from, "%Y-%m-%d") if d_from else None
        hi = datetime.strptime(d_to, "%Y-%m-%d") + timedelta(days=1) if d_to else None
    except ValueError:
        lo = hi = None
    out = []
    for r in sorted(rows, key=lambda r: r["LogID"], reverse=True):
        label = audit.label(r["ActionName"])
        if q_user and q_user.lower() not in (r["Username"] or "").lower():
            continue
        if q_action and q_action not in label and q_action not in (r["ActionName"] or ""):
            continue
        if lo and (not r["LoggedAt"] or r["LoggedAt"] < lo):
            continue
        if hi and (not r["LoggedAt"] or r["LoggedAt"] >= hi):
            continue
        r["Label"] = label
        out.append(r)
        if len(out) >= 500:
            break
    return render_template("settings_audit.html", tab="audit", rows=out, q_user=q_user, q_action=q_action,
                           d_from=d_from, d_to=d_to, total=len(rows))


# ------------------------------------------------------------------ projects (one official spelling for each)
@route("/settings/projects")
def settings_projects():
    use = projects.usage()
    rows = []
    for p in projects.all_projects():
        n_orders, n_contracts = use.get(p["ProjectName"], (0, 0))
        rows.append(dict(p, orders=n_orders, contracts=n_contracts))
    unknown = sorted(n for n in use if n and n not in {p["ProjectName"] for p in rows})
    return render_template("settings_projects.html", tab="projects", rows=rows, unknown=unknown)


@route("/settings/projects/new", methods=["POST"])
def project_add():
    name, created = projects.canonical(request.form.get("name", ""))
    if not name:
        flash("اكتب اسم المشروع.")
    else:
        flash("تمت إضافة المشروع «%s»." % name if created else "المشروع موجود بالفعل باسم «%s»." % name)
        if created:
            audit.log("project_add", "مشروع", name)
    return redirect(url_for("settings_projects"))


@route("/settings/projects/<int:project_id>/rename", methods=["POST"])
def project_rename(project_id):
    err = projects.rename(project_id, request.form.get("name", ""))
    flash(err or "تمت إعادة التسمية وتحديث كل الأوامر والعقود.")
    if not err:
        audit.log("project_rename", "مشروع", project_id, request.form.get("name", "")[:100])
    return redirect(url_for("settings_projects"))


@route("/settings/projects/merge", methods=["POST"])
def project_merge():
    err = projects.merge(request.form.get("source", type=int), request.form.get("target", type=int))
    flash(err or "تم الدمج: انتقلت كل أوامر وعقود المشروع المصدر إلى المشروع الهدف.")
    if not err:
        audit.log("project_merge", "مشروع", request.form.get("source", ""), "-> %s" % request.form.get("target", ""))
    return redirect(url_for("settings_projects"))


@route("/settings/projects/<int:project_id>/toggle", methods=["POST"])
def project_toggle(project_id):
    row = next((p for p in projects.all_projects() if p["ProjectID"] == project_id), None)
    if row:
        projects.set_active(project_id, not row["IsActive"])
        audit.log("project_toggle", "مشروع", row["ProjectName"], "active" if not row["IsActive"] else "inactive")
    return redirect(url_for("settings_projects"))


# ------------------------------------------------------------------ sites (one official name for each, under its project)
@route("/settings/sites")
def settings_sites():
    f_project = request.args.get("project", type=int)
    all_ = sites_service.all_sites()
    rows = [s for s in all_ if not f_project or s["ProjectID"] == f_project]
    return render_template("settings_sites.html", tab="sites", rows=rows, all_sites=all_, projects=projects.all_projects(), f_project=f_project)


@route("/settings/sites/<int:site_id>/rename", methods=["POST"])
def site_rename(site_id):
    err = sites_service.rename(site_id, request.form.get("name", ""))
    flash(err or "تمت إعادة التسمية وتحديث الأوامر والعقود والمستخلصات.")
    if not err:
        audit.log("site_rename", "موقع", site_id, request.form.get("name", "")[:100])
    return redirect(request.referrer or url_for("settings_sites"))


@route("/settings/sites/merge", methods=["POST"])
def site_merge():
    err = sites_service.merge(request.form.get("source", type=int), request.form.get("target", type=int))
    flash(err or "تم الدمج: انتقلت كل أوامر وعقود ومستخلصات الموقع المصدر إلى الموقع الهدف.")
    if not err:
        audit.log("site_merge", "موقع", request.form.get("source", ""), "-> %s" % request.form.get("target", ""))
    return redirect(request.referrer or url_for("settings_sites"))


@route("/settings/sites/<int:site_id>/toggle", methods=["POST"])
def site_toggle(site_id):
    row = next((s for s in sites_service.all_sites() if s["SiteID"] == site_id), None)
    if row:
        sites_service.set_active(site_id, not row["IsActive"])
    return redirect(request.referrer or url_for("settings_sites"))


# ------------------------------------------------------------------ archive health
@route("/settings/archive")
def settings_archive():
    import archive_health
    return render_template("settings_archive.html", tab="archive", r=archive_health.scan(), root=config.ARCHIVE_ROOT)

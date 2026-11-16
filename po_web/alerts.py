# -*- coding: utf-8 -*-
"""«التنبيهات» (29.0) - what needs somebody's action, shown inside the system: the bell at the top of every page and the alerts page.

Each kind of alert (RULES) has settings kept in CompanyProfile.AlertsJson: switched on or off, its number of days, and who receives it -
roles (by their key) and / or people by name (user ids). A person sees an alert only if it is addressed to them AND they may open the page
it points to (the permission is checked again: an alert never shows what the role cannot see).
Each alert has a stable key («late:PO-2026-445»): marking it «تم» is remembered per person (NotificationReads), and an alert that comes
back with a new state gets a new key (a certificate that is still stuck a week later is not hidden by last week's «تم»).
Nothing here writes to the business tables, and a failing rule never breaks a page: that rule is left out.
"""
import json
import logging
import threading
import time
from datetime import datetime, timedelta

import db

log = logging.getLogger(__name__)

# key: (title, description, default days (None = no days), what the days mean, default roles, permission to see, level, default on)
RULES = {
    "approval": ("اعتماد بانتظارك", "أمر صرف مسودة ينتظر الاعتماد، أو دورة صرف بانتظار قرار الإدارة، أو مستخدم جديد ينتظر الموافقة",
                 None, "", ["admin", "finance"], None, "warning", True),
    "late_delivery": ("توريد متأخر", "تجاوز تاريخ التوريد المتفق عليه ولم يكتمل الاستلام", 0, "أيام سماح بعد الموعد",
                      ["admin", "procurement"], "orders.view", "danger", True),
    "unpaid_invoice": ("فاتورة بلا سداد", "فاتورة صادرة مضى عليها عدد الأيام المحدد دون سداد", 30, "يوماً من تاريخ الفاتورة",
                       ["finance"], "orders.view", "warning", True),
    "expiring_docs": ("وثيقة تنتهي قريباً", "سجل تجاري أو شهادة أو أي ورقة لها تاريخ انتهاء لمورد أو مقاول", 30, "يوماً قبل الانتهاء",
                      ["procurement", "contracts"], None, "warning", True),
    "retention_due": ("استرداد المحتجز", "موعد رد محتجز ضمان الأعمال لعقد مقاول من الباطن (يُكتب في صفحة العقد)", 30, "يوماً قبل الموعد",
                      ["contracts", "finance"], "contracts.view", "info", True),
    "stalled_cert": ("مستخلص متوقف", "مستخلص داخل دورة صرف لم تُغلق، مضى على الدورة عدد الأيام المحدد", 7, "أيام",
                     ["contracts", "admin"], "cycles.view", "warning", True),
    "missing_files": ("أمر ناقص المرفقات", "أمر شراء بلا ملف «أمر الشراء» أو «عرض السعر» في أرشيفه بعد عدد الأيام المحدد", 3, "أيام من تاريخ الأمر",
                      ["procurement"], "orders.view", "info", False),
    "backup": ("النسخ الاحتياطي", "لم تؤخذ نسخة احتياطية حديثة، أو فشلت آخر نسخة", None, "", ["admin"], "settings.system", "danger", True),
}
LEVEL_ORDER = {"danger": 0, "warning": 1, "info": 2}


# ------------------------------------------------------------------ settings
def defaults():
    return {k: {"on": r[7], "days": r[2], "roles": list(r[4]), "users": []} for k, r in RULES.items()}


def settings():
    out = defaults()
    try:
        rows = db.query("SELECT [AlertsJson] FROM [CompanyProfile] WHERE [ProfileID] = 1")
        saved = json.loads(rows[0]["AlertsJson"]) if rows and rows[0].get("AlertsJson") else {}
    except Exception:                                           # noqa - before m035: the defaults
        saved = {}
    for k, cfg in (saved or {}).items():
        if k in out and isinstance(cfg, dict):
            out[k]["on"] = bool(cfg.get("on", out[k]["on"]))
            if out[k]["days"] is not None and isinstance(cfg.get("days"), int) and 0 <= cfg["days"] <= 365:
                out[k]["days"] = cfg["days"]
            out[k]["roles"] = [str(x) for x in cfg.get("roles", out[k]["roles"]) if isinstance(x, str)]
            out[k]["users"] = [int(x) for x in cfg.get("users", []) if str(x).isdigit()]
    return out


def save_settings(form):
    """form: on_<key>, days_<key>, roles_<key> (several), users_<key> (several). Returns (settings, errors)."""
    out, errors = {}, []
    for k, r in RULES.items():
        cfg = {"on": form.get("on_" + k) == "1", "days": None,
               "roles": sorted(set(form.getlist("roles_" + k))), "users": sorted({int(x) for x in form.getlist("users_" + k) if str(x).isdigit()})}
        if r[2] is not None:
            raw = (form.get("days_" + k) or "").strip()
            if not raw.isdigit() or not 0 <= int(raw) <= 365:
                errors.append("«%s»: عدد الأيام رقم من 0 إلى 365." % r[0])
                cfg["days"] = r[2]
            else:
                cfg["days"] = int(raw)
        if cfg["on"] and not cfg["roles"] and not cfg["users"]:
            errors.append("«%s» مفعّل ولم تختر له دوراً ولا شخصاً يصل إليه." % r[0])
        out[k] = cfg
    if errors:
        return None, errors
    text = json.dumps(out, ensure_ascii=False)
    if db.query("SELECT [ProfileID] FROM [CompanyProfile] WHERE [ProfileID] = 1"):
        db.execute("UPDATE [CompanyProfile] SET [AlertsJson] = ? WHERE [ProfileID] = 1", (text,))
    else:
        db.execute("INSERT INTO [CompanyProfile] ([ProfileID], [AlertsJson]) VALUES (1, ?)", (text,))
    clear_cache()
    return out, []


# ------------------------------------------------------------------ the rules (each returns [(key, text, endpoint, url kwargs, extra perm)])
def _today():
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)


def _approval(cfg):
    out = []
    for v in db.query("SELECT [VoucherID], [VoucherNo], [PartyName], [Net] FROM [PaymentVouchers] WHERE [Status] = ?", ("مسودة",)):
        out.append(("approval:voucher:%s" % v["VoucherID"], "أمر الصرف %s لـ «%s» بمبلغ %s ينتظر الاعتماد" % (v["VoucherNo"], v["PartyName"] or "", "{:,.2f}".format(float(v["Net"] or 0))),
                    "voucher_view", {"voucher_id": v["VoucherID"]}, "vouchers.approve"))
    import cycles_service as cy
    for c in db.query("SELECT [CycleID], [CycleNo] FROM [PaymentCycles] WHERE [Status] = ?", (cy.ST_WAITING,)):
        out.append(("approval:cycle:%s" % c["CycleID"], "دورة الصرف رقم %s بانتظار قرار الإدارة" % c["CycleNo"], "cycle_detail", {"cycle_id": c["CycleID"]}, "cycles.manage"))
    n = len(db.query("SELECT [UserID] FROM [Users] WHERE [Status] = ?", ("pending",)))
    if n:
        out.append(("approval:users:%d" % n, "%d مستخدم ينتظر الموافقة على تسجيله" % n, "settings_users", {}, "settings.users"))
    return out


def _late_delivery(cfg):
    limit = _today() - timedelta(days=cfg["days"] or 0)
    out = []
    for o in db.query("SELECT [PONumber], [SupplierName], [AgreedDeliveryDate] FROM [PurchaseOrders] WHERE [AgreedDeliveryDate] IS NOT NULL "
                      "AND [AgreedDeliveryDate] < ? AND ([ReceiptStatus] IS NULL OR [ReceiptStatus] <> ?)", (limit, "مكتمل")):
        days = (_today() - o["AgreedDeliveryDate"].replace(hour=0, minute=0, second=0, microsecond=0)).days
        out.append(("late:%s" % o["PONumber"], "أمر الشراء %s (%s) تأخر توريده %d يوماً" % (o["PONumber"], o["SupplierName"] or "", days),
                    "order_detail", {"po_number": o["PONumber"]}, None))
    return out


def _unpaid_invoice(cfg):
    limit = _today() - timedelta(days=cfg["days"] or 0)
    out = []
    for o in db.query("SELECT [PONumber], [SupplierName], [InvoiceDate] FROM [PurchaseOrders] WHERE [InvoiceStatus] = ? AND [InvoiceDate] IS NOT NULL "
                      "AND [InvoiceDate] < ? AND ([PaymentStatus] IS NULL OR [PaymentStatus] <> ?)", ("تم الإصدار", limit, "تم السداد")):
        days = (_today() - o["InvoiceDate"].replace(hour=0, minute=0, second=0, microsecond=0)).days
        out.append(("unpaid:%s" % o["PONumber"], "فاتورة أمر الشراء %s (%s) بلا سداد منذ %d يوماً" % (o["PONumber"], o["SupplierName"] or "", days),
                    "order_detail", {"po_number": o["PONumber"]}, None))
    return out


def _expiring_docs(cfg):
    limit = _today() + timedelta(days=cfg["days"] or 0)
    names = {"SUPPLIER": {r["SupplierID"]: r["Name"] for r in db.query("SELECT [SupplierID], [Name] FROM [Suppliers]")},
             "SUB": {r["SubcontractorID"]: r["Name"] for r in db.query("SELECT [SubcontractorID], [Name] FROM [Subcontractors]")}}
    out = []
    for d in db.query("SELECT [DocID], [EntityType], [EntityKey], [DocType], [ExpiryDate], [IsCurrent] FROM [Documents] WHERE [ExpiryDate] IS NOT NULL AND [ExpiryDate] <= ?", (limit,)):
        if not d.get("IsCurrent") or d["EntityType"] not in names:
            continue
        try:
            key = int(d["EntityKey"])
        except (TypeError, ValueError):
            continue
        who = names[d["EntityType"]].get(key)
        if not who:
            continue
        days = (d["ExpiryDate"].replace(hour=0, minute=0, second=0, microsecond=0) - _today()).days
        when = ("انتهى منذ %d يوماً" % -days) if days < 0 else ("ينتهي اليوم" if days == 0 else "ينتهي بعد %d يوماً" % days)
        if d["EntityType"] == "SUPPLIER":
            out.append(("doc:%s:%s" % (d["DocID"], "x" if days < 0 else "s"), "«%s» للمورد %s %s" % (d["DocType"], who, when), "supplier_detail", {"supplier_id": key}, "suppliers.view"))
        else:
            out.append(("doc:%s:%s" % (d["DocID"], "x" if days < 0 else "s"), "«%s» للمقاول %s %s" % (d["DocType"], who, when), "subcontractor_detail", {"sub_id": key}, "subcontractors.view"))
    return out


def _retention_due(cfg):
    limit = _today() + timedelta(days=cfg["days"] or 0)
    out = []
    for c in db.query("SELECT [ContractID], [ContractNo], [SubcontractorID], [RetentionDueDate] FROM [Contracts] WHERE [RetentionDueDate] IS NOT NULL AND [RetentionDueDate] <= ?", (limit,)):
        days = (c["RetentionDueDate"].replace(hour=0, minute=0, second=0, microsecond=0) - _today()).days
        if days < -60:                                          # long past: it was surely handled (or it is not news any more)
            continue
        when = ("حلّ منذ %d يوماً" % -days) if days < 0 else ("يحل اليوم" if days == 0 else "يحل بعد %d يوماً" % days)
        out.append(("retention:%s:%s" % (c["ContractID"], "x" if days < 0 else "s"), "موعد استرداد محتجز العقد %s %s" % (c.get("ContractNo") or "#%s" % c["ContractID"], when),
                    "contract_detail", {"contract_id": c["ContractID"]}, None))
    return out


def _stalled_cert(cfg):
    import cycles_service as cy
    limit = _today() - timedelta(days=cfg["days"] or 0)
    out = []
    open_cycles = {c["CycleID"]: c for c in db.query("SELECT [CycleID], [CycleNo], [Status], [CycleDate], [CreatedAt], [SentToMgmtAt] FROM [PaymentCycles]")
                   if c["Status"] not in (cy.ST_DONE, cy.ST_CANCELLED)}
    for cid, c in open_cycles.items():
        since = c.get("SentToMgmtAt") or c.get("CycleDate") or c.get("CreatedAt")
        if not since or since >= limit:
            continue
        n = len(db.query("SELECT [CertificateID] FROM [Certificates] WHERE [CycleID] = ? AND [ClosedAt] IS NULL", (cid,)))
        if n:
            days = (_today() - since.replace(hour=0, minute=0, second=0, microsecond=0)).days
            out.append(("stalled:%s:%d" % (cid, days // 7), "%d مستخلص في دورة الصرف رقم %s لم تتحرك منذ %d يوماً" % (n, c["CycleNo"], days),
                        "cycle_detail", {"cycle_id": cid}, None))
    return out


def _missing_files(cfg):
    limit = _today() - timedelta(days=cfg["days"] or 0)
    have = {}
    for d in db.query("SELECT [EntityKey], [DocType] FROM [Documents] WHERE [EntityType] = ? AND [IsCurrent] = True", ("PO",)):
        have.setdefault(d["EntityKey"], set()).add(d["DocType"])
    out = []
    for o in db.query("SELECT [PONumber], [OrderDate] FROM [PurchaseOrders] WHERE [OrderDate] < ?", (limit,)):
        miss = [t for t in ("أمر الشراء", "عرض السعر") if t not in have.get(o["PONumber"], set())]
        if miss:
            out.append(("files:%s" % o["PONumber"], "أمر الشراء %s بلا %s في الأرشيف" % (o["PONumber"], " و".join("«%s»" % m for m in miss)),
                        "order_detail", {"po_number": o["PONumber"]}, None))
    return out


def _backup(cfg):
    import backup
    return [("backup:%d" % i, "النسخ الاحتياطي: " + w, "settings_archive", {}, None) for i, w in enumerate(backup.status().get("warnings", []))]


CHECKS = {"approval": _approval, "late_delivery": _late_delivery, "unpaid_invoice": _unpaid_invoice, "expiring_docs": _expiring_docs,
          "retention_due": _retention_due, "stalled_cert": _stalled_cert, "missing_files": _missing_files, "backup": _backup}


# ------------------------------------------------------------------ for one person
_cache, _lock = {}, threading.Lock()
TTL = 60


def clear_cache():
    with _lock:
        _cache.clear()


def _all_alerts():
    """{rule: [alerts]} for every switched-on rule (shared by everybody, cached a minute)."""
    with _lock:
        hit = _cache.get("all")
        if hit and time.monotonic() - hit[0] < TTL:
            return hit[1]
    cfg = settings()
    found = {}
    for k, check in CHECKS.items():
        if not cfg[k]["on"]:
            continue
        try:
            found[k] = check(cfg[k])
        except Exception:                                       # noqa - one broken rule never hides the others or breaks a page
            log.exception("alert rule %s failed", k)
    with _lock:
        _cache["all"] = (time.monotonic(), found)
    return found


def for_user(user, can, url_for):
    """[{key, rule, title, text, url, level, read}] newest-first by level. `can` is auth.can (checked for the person signed in)."""
    if not user:
        return []
    cfg = settings()
    role_key = user.get("RoleKey") or ""
    read = set()
    try:
        read = {r["AlertKey"] for r in db.query("SELECT [AlertKey] FROM [NotificationReads] WHERE [UserID] = ?", (user["UserID"],))}
    except Exception:                                           # noqa - before m035
        pass
    out = []
    for k, alerts in _all_alerts().items():
        c = cfg[k]
        if role_key not in c["roles"] and user["UserID"] not in c["users"]:
            continue
        rule = RULES[k]
        if rule[5] and not can(rule[5]):
            continue
        for key, text, endpoint, kw, perm in alerts:
            if perm and not can(perm):
                continue
            try:
                url = url_for(endpoint, **kw)
            except Exception:                                   # noqa
                url = "#"
            out.append({"key": key, "rule": k, "title": rule[0], "text": text, "url": url, "level": rule[6], "read": key in read})
    out.sort(key=lambda a: (a["read"], LEVEL_ORDER.get(a["level"], 3)))
    return out


def mark_read(user_id, keys):
    keys = [k for k in keys if k][:500]
    if not keys:
        return 0
    have = {r["AlertKey"] for r in db.query("SELECT [AlertKey] FROM [NotificationReads] WHERE [UserID] = ?", (user_id,))}
    n = 0
    for k in keys:
        if k not in have:
            db.execute("INSERT INTO [NotificationReads] ([UserID], [AlertKey], [ReadAt]) VALUES (?, ?, ?)", (user_id, k[:200], datetime.now()))
            n += 1
    return n

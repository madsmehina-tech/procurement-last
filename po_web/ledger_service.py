# -*- coding: utf-8 -*-
"""
ledger_service.py - the contractor's account statement (كشف الحساب) and the movements typed by hand.

    مدين  (debit)  = what the company OWES the contractor: every certificate (its net due), and manual entitlements
                     (رد خصم، رد ضريبة، استحقاق آخر).
    دائن  (credit) = what was PAID or CHARGED to him: every executed transfer, and manual entries
                     (دفعة مباشرة، مواد على المقاول، تسوية).
    الرصيد = credit - debit      negative: the contractor still has dues from us · positive: we paid more than due · zero: settled

ONLY executed movements appear: a payment recorded but not executed yet (Status «مسجّلة») is not in the statement until
«تم التحويل». A transfer made through a payment cycle is ONE line of the statement («دفعة رقم N بتاريخ ...») with its wording.
"""
from datetime import datetime

import contracts_service as cs
import db
import masterdata
from finance import ST_PAID, ST_REJECTED
from textnorm import clean_spaces, norm_key

K_CREDIT, K_DEBIT = "دائن", "مدين"
CAT_PAYMENT = "دفعة مباشرة"
CATEGORIES = {K_CREDIT: [CAT_PAYMENT, "مواد على المقاول", "تسوية أخرى"], K_DEBIT: ["رد خصم", "رد ضريبة", "استحقاق آخر"]}
S_DONE, S_PENDING = "منفّذة", "مسجّلة"
EPS = 0.005


class LedgerError(Exception):
    """A rule was broken - the message is shown as is."""


def _r2(x):
    return round(float(x or 0), 2)


def _day(d):
    return d.strftime("%d/%m/%Y") if d else ""


def next_pay_no(sub_id, tx=None):
    """The next payment number of a contractor (stored when the payment is executed, never changed afterwards)."""
    q = (tx or db).query
    n = 0
    try:
        for r in q("SELECT [PayNo] FROM [Payments] WHERE [SubcontractorID] = ?", (sub_id,)):
            n = max(n, r.get("PayNo") or 0)
        for r in q("SELECT [PayNo] FROM [LedgerEntries] WHERE [SubcontractorID] = ?", (sub_id,)):
            n = max(n, r.get("PayNo") or 0)
    except Exception:                                    # noqa - before the migration the columns do not exist
        pass
    return n + 1


# ---------------------------------------------------------------- the entries typed by hand
def entries(sub_id):
    try:
        rows = db.query("SELECT * FROM [LedgerEntries] WHERE [SubcontractorID] = ?", (sub_id,))
    except Exception:                                    # noqa - before the migration the table does not exist
        return []
    return sorted(rows, key=lambda r: (r.get("EntryDate") or datetime.min, r["EntryID"]), reverse=True)


def add_entry(sub_id, kind, category, description, amount, when, project, site, executed, bank_ref, user):
    """Saves one movement. A direct payment may be saved as not executed yet; every other movement is executed at once."""
    kind, category, description = (kind or "").strip(), (category or "").strip(), clean_spaces(description)
    if kind not in CATEGORIES or category not in CATEGORIES[kind]:
        raise LedgerError("اختر النوع (دائن / مدين) والتصنيف.")
    if not description:
        raise LedgerError("اكتب وصف الحركة.")
    amount = _r2(amount)
    if amount <= 0:
        raise LedgerError("المبلغ يجب أن يكون أكبر من صفر.")
    status = S_DONE if (category != CAT_PAYMENT or executed) else S_PENDING
    import opening_service
    try:
        opening_service.guard_sub_date(sub_id, when or datetime.now(), "هذه الحركة")
    except opening_service.OpeningError as e:
        raise LedgerError(str(e))
    project, site = clean_spaces(project), clean_spaces(site)
    if site and not project:
        raise LedgerError("اكتب اسم المشروع مع الموقع.")
    with db.transaction() as tx:
        pid = sid = None
        if project:
            pid, project, _n = masterdata.resolve_project(project, tx)
            if site:
                sid, site, _n = masterdata.resolve_site(pid, site, tx)
        pay_no = next_pay_no(sub_id, tx) if (status == S_DONE and category == CAT_PAYMENT) else None      # an executed payment gets its number now
        return tx.execute_insert(
            "INSERT INTO [LedgerEntries] ([SubcontractorID], [Kind], [Category], [Description], [Amount], [EntryDate], [ProjectID], [SiteID], [ProjectName], [SiteName], "
            "[Status], [ExecAt], [BankRef], [PayNo], [CreatedBy], [CreatedAt]) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sub_id, kind, category, description[:250], amount, when or datetime.now(), pid, sid, project or None, site or None, status,
             datetime.now() if status == S_DONE else None, (bank_ref or "")[:80], pay_no, user, datetime.now()))


SETTLEMENT_CATEGORY = "تسوية أخرى"


def settle_on_close(tx, cert, amount, user):
    """The transaction of a certificate is closed while `amount` is still unpaid: ONE visible credit line in the contractor's statement settles it
    (it is not a bank payment and has no payment number). Reopening the transaction removes exactly this line (see remove_settlement)."""
    amount = _r2(amount)
    if amount <= 0:
        return None
    text = "تسوية عند إقفال المعاملة: المتبقي من %s لم يُصرف وأُقفلت المعاملة" % _cert_text(cert)
    return tx.execute_insert(
        "INSERT INTO [LedgerEntries] ([SubcontractorID], [Kind], [Category], [Description], [Amount], [EntryDate], [ProjectID], [SiteID], [ProjectName], [SiteName], "
        "[Status], [ExecAt], [SettledCertID], [CreatedBy], [CreatedAt]) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (cert["SubcontractorID"], K_CREDIT, SETTLEMENT_CATEGORY, text[:250], amount, datetime.now(), cert.get("ProjectID"), cert.get("SiteID"),
         cert.get("Project") or None, cert.get("SiteName") or None, S_DONE, datetime.now(), cert["CertificateID"], user, datetime.now()))


def remove_settlement(tx, cert_id):
    """Reopening a closed transaction takes back the settlement line it created. Returns the amount that was settled (0 when there was none)."""
    rows = tx.query("SELECT [EntryID], [Amount] FROM [LedgerEntries] WHERE [SettledCertID] = ?", (cert_id,))
    for r in rows:
        tx.execute("DELETE FROM [LedgerEntries] WHERE [EntryID] = ?", (r["EntryID"],))
    return _r2(sum(r["Amount"] or 0 for r in rows))


def execute_entry(entry_id, when, bank_ref, user):
    """«تم التحويل» for a direct payment that was only recorded: from now on it is in the statement."""
    rows = db.query("SELECT * FROM [LedgerEntries] WHERE [EntryID] = ?", (entry_id,))
    if not rows:
        raise LedgerError("الحركة غير موجودة.")
    e = rows[0]
    if e["Status"] != S_PENDING:
        raise LedgerError("هذه الحركة منفّذة بالفعل.")
    if e.get("CycleID"):
        raise LedgerError("هذه الدفعة داخل دورة صرف مفتوحة؛ حدّد «تم التحويل» من صفحة الدورة.")
    db.execute("UPDATE [LedgerEntries] SET [Status] = ?, [EntryDate] = ?, [ExecAt] = ?, [BankRef] = ?, [PayNo] = ?, [ArchiveFolder] = NULL WHERE [EntryID] = ?",
               (S_DONE, when or datetime.now(), datetime.now(), (bank_ref or "")[:80], next_pay_no(e["SubcontractorID"]), entry_id))
    return e["SubcontractorID"]


def delete_entry(entry_id):
    rows = db.query("SELECT * FROM [LedgerEntries] WHERE [EntryID] = ?", (entry_id,))
    if not rows:
        raise LedgerError("الحركة غير موجودة.")
    e = rows[0]
    if e.get("CycleID"):
        raise LedgerError("هذه الحركة مرتبطة بدورة صرف فلا تُحذف.")
    if e.get("SettledCertID"):
        raise LedgerError("هذه «تسوية عند إقفال المعاملة»؛ تُلغى بإعادة فتح المعاملة من الأرشيف (بصلاحية أعلى).")
    if e.get("Category") == "رصيد افتتاحي":
        raise LedgerError("هذا «رصيد الافتتاح»؛ يُعدَّل أو يُلغى من بطاقة «رصيد الافتتاح» لا من قائمة الحركات.")
    db.execute("DELETE FROM [LedgerEntries] WHERE [EntryID] = ?", (entry_id,))
    return e["SubcontractorID"]


# ---------------------------------------------------------------- the statement
def _cert_text(g):
    return "مستخلص رقم %s لمشروع %s%s" % (g["CertificateNo"], g.get("Project") or "", (" - موقع " + g["SiteName"]) if g.get("SiteName") else "")


def statement(sub_id, date_from=None, date_to=None, project="", site=""):
    """{lines, opening, debit, credit, balance, note, projects, sites}. Lines: seq, date, desc, debit, credit, balance."""
    subs, _contracts, certs, pays = cs.load()
    mine_certs = {g["CertificateID"]: g for g in certs if g["SubcontractorID"] == sub_id}
    filtered = bool(project or site)
    pk, sk = norm_key(project), norm_key(site)

    def matches(proj, st):
        return (not pk or norm_key(proj) == pk) and (not sk or norm_key(st) == sk)

    items = []                                           # (date, order, id, desc, debit, credit, project, site)
    for g in mine_certs.values():                         # DEBIT: the certificates
        if g["Status"] == ST_REJECTED:
            continue
        items.append({"date": g.get("CertificateDate") or g.get("CreatedAt") or datetime.min, "o": 0, "id": g["CertificateID"], "desc": _cert_text(g),
                      "debit": _r2(g["Total"]), "credit": 0.0, "project": g.get("Project") or "", "site": g.get("SiteName") or ""})
    led = [e for e in entries(sub_id) if e["Status"] == S_DONE]
    # the payments: one event per cycle transfer (a batch), one per old direct payment, one per executed direct entry
    events = {}
    for p in pays:
        if p["CertificateID"] not in mine_certs:
            continue
        when = p.get("PaidAt") or datetime.min
        key = ("c", p["CycleID"], when.date()) if p.get("CycleID") else ("p", p["PaymentID"])
        ev = events.setdefault(key, {"date": when, "rows": [], "id": p["PaymentID"], "statement": p.get("Statement") or ""})
        ev["rows"].append(p)
        ev["date"] = max(ev["date"], when)
    for g in mine_certs.values():                         # paid in an older version of the system: no payment record, but the money was paid
        if g["Status"] == ST_PAID and not any(p["CertificateID"] == g["CertificateID"] for p in pays):
            events[("l", g["CertificateID"])] = {"date": g.get("PaymentDate") or g.get("CertificateDate") or g.get("CreatedAt") or datetime.min, "legacy": g, "id": g["CertificateID"]}
    for e in led:
        if e["Kind"] == K_CREDIT and e["Category"] == CAT_PAYMENT:
            when = e.get("EntryDate") or datetime.min
            if e.get("CycleID") and not filtered:         # executed through a cycle: the SAME transfer as the cycle's certificate payments
                ev = events.setdefault(("c", e["CycleID"], when.date()), {"date": when, "rows": [], "id": e["EntryID"], "statement": ""})
                ev.setdefault("entries", []).append(e)
                ev["date"] = max(ev["date"], when)
            else:
                events[("e", e["EntryID"])] = {"date": when, "entry": e, "id": e["EntryID"]}
    def stored_no(ev):
        if "entry" in ev:
            return ev["entry"].get("PayNo")
        return next((x.get("PayNo") for x in list(ev.get("rows", [])) + list(ev.get("entries", [])) if x.get("PayNo")), None)
    number = {k: stored_no(ev) for k, ev in events.items()}
    for key, ev in events.items():
        n = number[key]
        head = ("دفعة رقم %d بتاريخ %s" % (n, _day(ev["date"]))) if n else ("دفعة بتاريخ %s" % _day(ev["date"]))
        if "legacy" in ev:
            g = ev["legacy"]
            if matches(g.get("Project") or "", g.get("SiteName") or ""):
                items.append({"date": ev["date"], "o": 1, "id": ev["id"], "desc": "%s — %s (مصروف قبل تسجيل الدفعات)" % (head, _cert_text(g)), "debit": 0.0, "credit": _r2(g["Total"]),
                              "project": g.get("Project") or "", "site": g.get("SiteName") or ""})
        elif "entry" in ev:
            e = ev["entry"]
            if matches(e.get("ProjectName") or "", e.get("SiteName") or ""):
                items.append({"date": ev["date"], "o": 1, "id": ev["id"], "desc": "%s — %s" % (head, e["Description"]), "debit": 0.0, "credit": _r2(e["Amount"]),
                              "project": e.get("ProjectName") or "", "site": e.get("SiteName") or ""})
        elif not filtered and key[0] == "c":
            ents = ev.get("entries", [])
            wording = ev["statement"] or "؛ ".join("دفعة: %s" % e["Description"] for e in ents)
            items.append({"date": ev["date"], "o": 1, "id": ev["id"], "desc": head + ((" — " + wording) if wording else ""), "debit": 0.0,
                          "credit": _r2(sum(r["Amount"] or 0 for r in ev["rows"]) + sum(e["Amount"] or 0 for e in ents)), "project": "", "site": ""})
        else:                                             # one line per certificate paid: a project / site filter needs them apart
            for e in ev.get("entries", []):
                if matches(e.get("ProjectName") or "", e.get("SiteName") or ""):
                    items.append({"date": ev["date"], "o": 1, "id": e["EntryID"], "desc": "%s — %s" % (head, e["Description"]), "debit": 0.0, "credit": _r2(e["Amount"]),
                                  "project": e.get("ProjectName") or "", "site": e.get("SiteName") or ""})
            for r in ev["rows"]:
                g = mine_certs[r["CertificateID"]]
                if matches(g.get("Project") or "", g.get("SiteName") or ""):
                    items.append({"date": ev["date"], "o": 1, "id": r["PaymentID"], "desc": "%s — %s" % (head, _cert_text(g)), "debit": 0.0, "credit": _r2(r["Amount"]),
                                  "project": g.get("Project") or "", "site": g.get("SiteName") or ""})
    for e in led:                                         # the other manual movements
        if e["Kind"] == K_CREDIT and e["Category"] == CAT_PAYMENT:
            continue
        if matches(e.get("ProjectName") or "", e.get("SiteName") or ""):
            items.append({"date": e.get("EntryDate") or datetime.min, "o": 0 if e["Kind"] == K_DEBIT else 1, "id": e["EntryID"], "desc": "%s — %s" % (e["Category"], e["Description"]),
                          "debit": _r2(e["Amount"]) if e["Kind"] == K_DEBIT else 0.0, "credit": _r2(e["Amount"]) if e["Kind"] == K_CREDIT else 0.0,
                          "project": e.get("ProjectName") or "", "site": e.get("SiteName") or ""})
    if filtered:
        items = [i for i in items if matches(i["project"], i["site"])]
    items.sort(key=lambda i: (i["date"], i["o"], i["id"]))
    opening = _r2(sum(i["credit"] - i["debit"] for i in items if date_from and i["date"] < date_from))
    shown = [i for i in items if (not date_from or i["date"] >= date_from) and (not date_to or i["date"] < date_to + _ONE_DAY)]
    lines, running = [], opening
    for n, i in enumerate(shown, 1):
        running = _r2(running + i["credit"] - i["debit"])
        lines.append(dict(i, seq=n, balance=running))
    debit, credit = _r2(sum(i["debit"] for i in shown)), _r2(sum(i["credit"] for i in shown))
    balance = _r2(opening + credit - debit)
    note = "المقاول له مستحقات علينا" if balance < -EPS else ("صُرف له أكثر من المستحق" if balance > EPS else "لا توجد فروقات")
    all_projects = sorted({g.get("Project") or "" for g in mine_certs.values()} | {e.get("ProjectName") or "" for e in led} - {""}, key=norm_key)
    all_sites = sorted({g.get("SiteName") or "" for g in mine_certs.values()} | {e.get("SiteName") or "" for e in led} - {""}, key=norm_key)
    return {"sub": subs.get(sub_id) or {}, "lines": lines, "opening": opening, "debit": debit, "credit": credit, "balance": balance, "note": note,
            "projects": all_projects, "sites": all_sites}


from datetime import timedelta  # noqa: E402
_ONE_DAY = timedelta(days=1)


def balance_of(sub_id):
    """The contractor's balance today (executed movements only)."""
    return statement(sub_id)["balance"]


def register(sub_id=None, year=None):
    """Every EXECUTED payment / movement with its archive folder and files. Returns (rows, contractors, years)."""
    subs, _contracts, certs, pays = cs.load()
    by_cert = {g["CertificateID"]: g for g in certs}
    try:
        cycle_no = {c["CycleID"]: c["CycleNo"] for c in db.query("SELECT [CycleID], [CycleNo] FROM [PaymentCycles]")}
        docs = [d for d in db.query("SELECT * FROM [Documents]") if d.get("IsCurrent") and d["EntityType"] in ("PAYMENT", "LEDGER")]
        all_entries = db.query("SELECT * FROM [LedgerEntries]")
    except Exception:                                    # noqa - before the migration
        return [], [], []
    docs_of = {}
    for d in docs:
        docs_of.setdefault((d["EntityType"], str(d["EntityKey"])), []).append(d)
    events = {}
    for p in pays:                                       # cycle transfers (one per contractor and cycle) and older direct payments
        g = by_cert.get(p["CertificateID"])
        sid = p.get("SubcontractorID") or (g or {}).get("SubcontractorID")
        if not g or not sid:
            continue
        key = (sid, "n", p["PayNo"]) if p.get("PayNo") else (sid, "p", p["PaymentID"])
        ev = events.setdefault(key, {"sub_id": sid, "pay_no": p.get("PayNo"), "date": p.get("PaidAt"), "kind": "تحويل دورة صرف رقم %s" % cycle_no.get(p.get("CycleID")) if p.get("CycleID") else "دفعة مباشرة على مستخلص",
                                     "amount": 0.0, "desc": p.get("Statement") or "", "folder": p.get("ArchiveFolder") or "", "docs": [], "certs": []})
        ev["amount"] = _r2(ev["amount"] + (p["Amount"] or 0))
        ev["certs"].append(g["CertificateNo"])
        if not ev["desc"]:
            ev["desc"] = _cert_text(g)
    for e in all_entries:
        if e["Status"] != S_DONE:
            continue
        is_pay = e["Kind"] == K_CREDIT and e["Category"] == CAT_PAYMENT and e.get("PayNo")
        key = (e["SubcontractorID"], "n", e["PayNo"]) if is_pay else (e["SubcontractorID"], "e", e["EntryID"])
        ev = events.setdefault(key, {"sub_id": e["SubcontractorID"], "pay_no": e.get("PayNo") if is_pay else None, "date": e.get("EntryDate"), "kind": "دفعة مباشرة" if is_pay else "%s (%s)" % (e["Category"], e["Kind"]),
                                     "amount": 0.0, "desc": "", "folder": e.get("ArchiveFolder") or "", "docs": [], "certs": []})
        ev["amount"] = _r2(ev["amount"] + (e["Amount"] or 0))
        ev["desc"] = (ev["desc"] + "؛ " if ev["desc"] else "") + e["Description"]
        ev["folder"] = ev["folder"] or e.get("ArchiveFolder") or ""
        ev["docs"] += docs_of.get(("LEDGER", str(e["EntryID"])), [])
    out = []
    for key, ev in events.items():
        if ev["pay_no"]:
            ev["docs"] += docs_of.get(("PAYMENT", "%s:%s" % (ev["sub_id"], ev["pay_no"])), [])
        ev["sub_name"] = (subs.get(ev["sub_id"]) or {}).get("Name", "")
        ev["year"] = ev["date"].year if ev["date"] else None
        ev["certs"] = sorted(set(ev["certs"]))
        if (not sub_id or ev["sub_id"] == sub_id) and (not year or ev["year"] == year):
            out.append(ev)
    out.sort(key=lambda e: (e["date"] or datetime.min), reverse=True)
    years = sorted({e["date"].year for e in events.values() if e["date"]}, reverse=True)
    return out, sorted(subs.values(), key=lambda s: s["Name"]), years

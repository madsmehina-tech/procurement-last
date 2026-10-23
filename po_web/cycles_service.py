# -*- coding: utf-8 -*-
"""
cycles_service.py - the payment cycle (دورة الصرف), built on the REAL workflow of the company.

Three separate things, never mixed:
    1. اعتماد المستخلص   - a certificate entered in the system counts as approved (the accounting review is done outside).
    2. قرار الإدارة      - what management decided for each certificate (يصرف بالكامل / يصرف جزئياً / مؤجل) and the amount.
    3. تنفيذ التحويل     - did the transfer really happen? ONLY «تم التحويل» makes an amount paid.

The cycle has three stages (the status of the cycle in the database):
    1 الإنشاء            (مسودة)                 the sheet is generated from every open certificate; notes only.
    2 قرار الإدارة       (بانتظار قرار الإدارة)   decision + amount + notes for every certificate.
    3 تنفيذ التحويل      (معتمدة)                everything is read-only except the execution state of each line:
                                                 «تم التحويل»  -> the amount is recorded as paid (Payments tab),
                                                 «لم يتم التنفيذ» -> nothing is paid, the certificate returns to the next cycle,
                                                 whatever the decision was.
    (منفّذة) when every line of stage 3 has an outcome the cycle is closed - it can still be reopened, edited, printed.

A certificate stays in the cycles until it is paid in full or its transaction is closed in the archive.
Every change runs in ONE database transaction and is written to CycleLog (full history, also after deletion).
"""
from datetime import datetime

import contracts_service as cs
import db
import ledger_service as ledger
from finance import ST_ENTERED, ST_IN_CYCLE, ST_PAID, row_amounts

ST_DRAFT, ST_WAITING, ST_APPROVED, ST_DONE, ST_CANCELLED = "مسودة", "بانتظار قرار الإدارة", "معتمدة", "منفّذة", "ملغاة"
CYCLE_STATUSES = (ST_DRAFT, ST_WAITING, ST_APPROVED, ST_DONE, ST_CANCELLED)
OPEN_STATUSES = (ST_DRAFT, ST_WAITING, ST_APPROVED)
CYCLE_BADGE = {ST_DRAFT: "neutral", ST_WAITING: "warning", ST_APPROVED: "info", ST_DONE: "success", ST_CANCELLED: "danger"}
STAGE_NAME = {ST_DRAFT: "مرحلة الإنشاء", ST_WAITING: "مرحلة قرار الإدارة", ST_APPROVED: "مرحلة تنفيذ التحويل", ST_DONE: "مغلقة (تم تنفيذها)", ST_CANCELLED: "ملغاة"}
D_FULL, D_PART, D_DEFER = "صرف كامل", "صرف جزئي", "تأجيل"
DECISIONS = (D_FULL, D_PART, D_DEFER)
DECISION_LABEL = {D_FULL: "يصرف بالكامل", D_PART: "يصرف جزئياً", D_DEFER: "مؤجل"}
EX_DONE, EX_NOT = "تم التحويل", "لم يتم التنفيذ"
EXEC_STATES = (EX_DONE, EX_NOT)
ENTRY_LABEL = "دفعة مباشرة"                         # a direct payment that was recorded and not executed yet, riding in the cycle
S_READY = "ready"                                   # the old per-line classification column is kept for compatibility, never decided by the system
STAGES = [("الإنشاء", "تجميع المستخلصات المفتوحة"), ("قرار الإدارة", "القرار والمبلغ لكل مستخلص"), ("تنفيذ التحويل", "تم التحويل / لم يتم التنفيذ")]
EPS = 0.005


class CycleError(Exception):
    """A rule of the process was broken - the message is shown to the person as is."""


def _r2(x):
    return round(float(x or 0), 2)


def order_no(cycle_no, seq):
    return "PAY-%s-%02d" % (cycle_no, seq)


def mask_iban(iban):
    s = (iban or "").replace(" ", "")
    return s if len(s) < 8 else "%s•• •••• •••• •••• •••• %s" % (s[:2], s[-4:])


def _age(g):
    when = g.get("CertificateDate") or g.get("CreatedAt")
    return max((datetime.now() - when).days, 0) if when else 0


def _money(x):
    return "{:,.2f}".format(_r2(x))


# ---------------------------------------------------------------- the history (nothing is ever lost)
def _log(tx, cycle, cert_id, action, detail, user):
    tx.execute("INSERT INTO [CycleLog] ([CycleID], [CycleNo], [CertificateID], [Action], [Detail], [ActionAt], [ActionBy]) VALUES (?, ?, ?, ?, ?, ?, ?)",
               (cycle["CycleID"], cycle.get("CycleNo"), cert_id, (action or "")[:60], (detail or "")[:250], datetime.now(), (user or "")[:50]))


def history(cycle_id):
    """The full log of one cycle, newest first."""
    try:
        rows = db.query("SELECT * FROM [CycleLog] WHERE [CycleID] = ?", (cycle_id,))
    except Exception:                                    # noqa - before the migration the table does not exist
        return []
    return sorted(rows, key=lambda r: r["LogID"], reverse=True)


# ---------------------------------------------------------------- what can enter a cycle
def _deferred_counts(exclude_cycle=None):
    out = {}
    cancelled = {c["CycleID"] for c in db.query("SELECT [CycleID], [Status] FROM [PaymentCycles]") if c["Status"] == ST_CANCELLED}
    for d in db.query("SELECT [CycleID], [CertificateID], [DecisionType] FROM [CycleDecisions]"):
        if d["DecisionType"] == D_DEFER and d["CycleID"] != exclude_cycle and d["CycleID"] not in cancelled:
            out[d["CertificateID"]] = out.get(d["CertificateID"], 0) + 1
    return out


def candidates(exclude_cycle=None):
    """Every certificate that is still open: not paid in full, not closed in the archive, not already inside another cycle."""
    _s, _contracts, certs, _p = cs.load()
    deferred = _deferred_counts(exclude_cycle)
    rows = []
    for g in certs:
        if g["Status"] != ST_ENTERED or g["Remaining"] <= EPS or g.get("ClosedAt") or (g.get("CycleID") and g["CycleID"] != exclude_cycle):
            continue
        rows.append({"cert": g, "age": _age(g), "deferred": deferred.get(g["CertificateID"], 0)})
    rows.sort(key=lambda r: (str(r["cert"].get("SubcontractorName") or ""), str(r["cert"].get("Project") or ""), str(r["cert"].get("SiteName") or ""),
                             r["cert"].get("CertificateDate") or r["cert"].get("CreatedAt") or datetime.min))
    return rows


def pending_entries():
    """Direct payments recorded («مسجّلة») and not executed, not inside another cycle: they can enter a new cycle."""
    try:
        rows = db.query("SELECT * FROM [LedgerEntries]")
    except Exception:                                    # noqa - before the migration the table does not exist
        return []
    return [e for e in rows if e["Kind"] == ledger.K_CREDIT and e["Category"] == ledger.CAT_PAYMENT and e["Status"] == ledger.S_PENDING and not e.get("CycleID")]


def current_cycle():
    rows = [c for c in db.query("SELECT * FROM [PaymentCycles]") if c["Status"] in OPEN_STATUSES]
    return max(rows, key=lambda c: c["CycleNo"] or 0) if rows else None


# ---------------------------------------------------------------- reading
def list_cycles():
    cycles = db.query("SELECT * FROM [PaymentCycles]")
    decs = db.query("SELECT [CycleID], [DecisionType], [RequestedAmount], [ApprovedAmount] FROM [CycleDecisions]")
    pays = db.query("SELECT [CycleID], [Amount] FROM [Payments]")
    try:
        pays = pays + [{"CycleID": e["CycleID"], "Amount": e["Amount"]} for e in db.query("SELECT * FROM [LedgerEntries]") if e.get("CycleID") and e["Status"] == ledger.S_DONE]
    except Exception:                                    # noqa
        pass
    out = []
    for c in cycles:
        mine = [d for d in decs if d["CycleID"] == c["CycleID"]]
        out.append(dict(c, lines=len(mine), requested=_r2(sum(d.get("RequestedAmount") or 0 for d in mine)),
                        approved=_r2(sum((d.get("ApprovedAmount") or 0) for d in mine if d["DecisionType"] in (D_FULL, D_PART))),
                        transferred=_r2(sum(p.get("Amount") or 0 for p in pays if p.get("CycleID") == c["CycleID"])),
                        stage_name=STAGE_NAME.get(c["Status"], c["Status"])))
    out.sort(key=lambda c: c.get("CycleNo") or 0, reverse=True)
    return out


def _line_status(deferred_before, paid_before):
    if deferred_before:
        return "مؤجل من الدورة السابقة" if deferred_before == 1 else "مؤجل للمرة %d" % (deferred_before + 1)
    if paid_before > EPS:
        return "مصروف جزئياً"
    return "جديد"


def payment_statement(lines):
    """The wording of ONE payment to a contractor («البيان»), decided by the real case:
       1. every open certificate paid in full (or one certificate paid in full)  -> «مستخلص رقم N لمشروع P»
       2. a part of the contractor's total, spread over several certificates      -> «دفعة من المستخلصات المستحقة»
       3. a part of ONE specific certificate                                      -> «دفعة من المستخلص رقم N لمشروع P»
       (a direct payment riding in the cycle adds «دفعة: <its description>»)"""
    certs = [l for l in lines if not l["IsEntry"]]
    payable = [l for l in certs if l["Payable"]]
    parts = []
    if payable:
        if all(l["Decision"] == D_FULL for l in certs) or (len(payable) == 1 and payable[0]["Decision"] == D_FULL):
            by_project = {}
            for l in payable:
                by_project.setdefault(l["Project"], []).append(l["CertNo"])
            for proj, nos in by_project.items():
                parts.append(("مستخلص رقم %s" % nos[0] if len(nos) == 1 else "مستخلصات رقم %s" % "، ".join(str(n) for n in nos)) + ((" لمشروع %s" % proj) if proj else ""))
        elif len(payable) == 1:
            l = payable[0]
            parts.append("دفعة من المستخلص رقم %s%s" % (l["CertNo"], (" لمشروع %s" % l["Project"]) if l["Project"] else ""))
        else:
            parts.append("دفعة من المستخلصات المستحقة")
    for l in lines:
        if l["IsEntry"] and l["Payable"]:
            parts.append("دفعة: %s" % l["Description"])
    return "؛ ".join(parts)


def _entry_rows():
    try:
        return {e["EntryID"]: e for e in db.query("SELECT * FROM [LedgerEntries]")}
    except Exception:                                    # noqa - before the migration the table does not exist
        return {}


def load(cycle_id):
    """The cycle with every line merged with its certificate (or direct payment), grouped by contractor."""
    rows = db.query("SELECT * FROM [PaymentCycles] WHERE [CycleID] = ?", (cycle_id,))
    if not rows:
        return None
    cycle = rows[0]
    subs, _contracts, certs, pays = cs.load()
    by_cert = {g["CertificateID"]: g for g in certs}
    entry_rows = _entry_rows()
    lines = []
    for d in db.query("SELECT * FROM [CycleDecisions] WHERE [CycleID] = ?", (cycle_id,)):
        decision = d.get("DecisionType") or ""
        requested = _r2(d.get("RequestedAmount"))
        if d.get("EntryID"):                                                        # a direct payment recorded and not executed yet
            e = entry_rows.get(d["EntryID"])
            if not e:
                continue
            sub = subs.get(e["SubcontractorID"]) or {}
            approved = _r2(d.get("ApprovedAmount")) if decision == D_FULL else 0.0
            done = d.get("ExecStatus") == EX_DONE
            lines.append({"DecisionID": d["DecisionID"], "CertificateID": None, "EntryID": e["EntryID"], "IsEntry": True, "Cert": {}, "SubName": sub.get("Name", ""),
                          "SubcontractorID": e["SubcontractorID"], "IBAN": sub.get("BankAccount") or "", "Project": e.get("ProjectName") or "", "Site": e.get("SiteName") or "",
                          "CertNo": "", "ContractNo": "", "Total": requested, "PaidBefore": 0.0, "Requested": requested, "DeferredBefore": 0, "StatusLabel": ENTRY_LABEL, "Age": 0,
                          "Type": ENTRY_LABEL, "Description": e["Description"], "Decision": decision, "DecisionLabel": DECISION_LABEL.get(decision, ""), "Approved": approved,
                          "Note": d.get("NoteText") or "", "Payable": decision == D_FULL and approved > EPS, "Exec": d.get("ExecStatus") or "", "ExecAt": d.get("ExecAt"),
                          "Transferred": approved if done else 0.0, "Pays": [], "Pay": None, "DecidedAt": d.get("DecidedAt"), "Closed": False})
            continue
        g = by_cert.get(d["CertificateID"])
        if not g:
            continue
        sub = subs.get(g["SubcontractorID"]) or {}
        approved = _r2(d.get("ApprovedAmount")) if decision in (D_FULL, D_PART) else 0.0
        mine = [p for p in pays if p.get("CycleID") == cycle_id and p["CertificateID"] == d["CertificateID"]]
        sent = _r2(sum(p.get("Amount") or 0 for p in mine))
        paid_before = _r2(g["Paid"] - sent)
        lines.append({"DecisionID": d["DecisionID"], "CertificateID": g["CertificateID"], "EntryID": None, "IsEntry": False, "Cert": g, "SubName": g["SubcontractorName"],
                      "SubcontractorID": g["SubcontractorID"], "IBAN": sub.get("BankAccount") or "", "Project": g["Project"] or "", "Site": g.get("SiteName") or "", "CertNo": g["CertificateNo"],
                      "ContractNo": g.get("ContractNo") or "", "Total": _r2(g["Total"]), "PaidBefore": paid_before, "Requested": requested,
                      "DeferredBefore": d.get("DeferredBefore") or 0, "StatusLabel": _line_status(d.get("DeferredBefore") or 0, paid_before), "Age": _age(g),
                      "Type": "مستخلص", "Description": "", "Decision": decision, "DecisionLabel": DECISION_LABEL.get(decision, ""), "Approved": approved, "Note": d.get("NoteText") or "",
                      "Payable": decision in (D_FULL, D_PART) and approved > EPS, "Exec": d.get("ExecStatus") or "", "ExecAt": d.get("ExecAt"),
                      "Transferred": sent, "Pays": mine, "Pay": (max(mine, key=lambda p: p["PaymentID"]) if mine else None),
                      "DecidedAt": d.get("DecidedAt"), "Closed": bool(g.get("ClosedAt"))})
    lines.sort(key=lambda l: (l["SubName"] or "", l["IsEntry"], l["DecisionID"]))      # one block per contractor; its certificates first, then its direct payments
    groups = []
    for l in lines:
        if not groups or groups[-1]["sub_id"] != l["SubcontractorID"]:
            groups.append({"sub_id": l["SubcontractorID"], "name": l["SubName"], "iban": l["IBAN"], "lines": []})
        groups[-1]["lines"].append(l)
    n_order = 0
    for gp in groups:                                    # «الإجمالي» (open certificates), the approved amount, and ONE payment order per contractor
        gp["total"] = _r2(sum(l["Requested"] for l in gp["lines"] if not l["IsEntry"]))
        gp["approved"] = _r2(sum(l["Approved"] for l in gp["lines"] if not l["IsEntry"]))
        gp["entries_total"] = _r2(sum(l["Approved"] for l in gp["lines"] if l["IsEntry"]))
        gp["decided"] = sum(1 for l in gp["lines"] if l["Decision"])
        pay_lines = [l for l in gp["lines"] if l["Payable"]]
        gp["payable"] = _r2(sum(l["Approved"] for l in pay_lines))
        gp["statement"] = payment_statement(gp["lines"])
        states = {l["Exec"] for l in pay_lines}
        gp["exec"] = (next(iter(states)) if len(states) == 1 else ("mixed" if states else "")) if pay_lines else ""
        gp["has_entries"] = any(l["IsEntry"] for l in gp["lines"])
        if pay_lines:
            n_order += 1
            gp["order_no"] = order_no(cycle["CycleNo"], n_order)
            for l in pay_lines:
                l["OrderNo"] = gp["order_no"]
        else:
            gp["order_no"] = ""
    payable = [l for l in lines if l["Payable"]]
    t = {"all": _r2(sum(l["Requested"] for l in lines)), "n_all": len(lines), "approved": _r2(sum(l["Approved"] for l in lines)),
         "decided": sum(1 for l in lines if l["Decision"]), "n_payable": len(payable), "n_defer": sum(1 for l in lines if l["Decision"] == D_DEFER),
         "executed": sum(1 for l in payable if l["Exec"] == EX_DONE), "not_executed": sum(1 for l in payable if l["Exec"] == EX_NOT),
         "unresolved": sum(1 for l in payable if not l["Exec"]), "transferred": _r2(sum(l["Transferred"] for l in lines)),
         "receipts": sum(1 for l in lines for p in l["Pays"] if p.get("DocID")),
         "orders": sum(1 for gp in groups if gp["payable"] > EPS), "orders_done": sum(1 for gp in groups if gp["payable"] > EPS and gp["exec"] == EX_DONE)}
    return {"cycle": cycle, "lines": lines, "groups": groups, "totals": t, "stage": stage_of(cycle), "log": history(cycle_id)}


def stage_of(cycle):
    """1..3 = the current stage; 4 = closed (all three finished); 0 = cancelled (old cycles only)."""
    return {ST_DRAFT: 1, ST_WAITING: 2, ST_APPROVED: 3, ST_DONE: 4}.get(cycle["Status"], 0)


def _cycle(tx, cycle_id):
    rows = tx.query("SELECT * FROM [PaymentCycles] WHERE [CycleID] = ?", (cycle_id,))
    if not rows:
        raise CycleError("دورة الصرف غير موجودة.")
    return rows[0]


def _need(cycle, *statuses):
    if cycle["Status"] not in statuses:
        raise CycleError("لا يمكن تنفيذ هذا الإجراء والدورة في «%s»." % STAGE_NAME.get(cycle["Status"], cycle["Status"]))


# ---------------------------------------------------------------- stage 1: create / refresh
def _insert_lines(tx, cycle_id, rows, entries=()):
    for r in rows:
        g = r["cert"]
        tx.execute("INSERT INTO [CycleDecisions] ([CycleID], [CertificateID], [DecisionType], [RequestedAmount], [StatusCode], [Reason], [DeferredBefore]) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?)", (cycle_id, g["CertificateID"], "", _r2(g["Remaining"]), S_READY, "", r["deferred"]))
        tx.execute("UPDATE [Certificates] SET [Status] = ?, [CycleID] = ? WHERE [CertificateID] = ?", (ST_IN_CYCLE, cycle_id, g["CertificateID"]))
    for e in entries:                                    # a direct payment already decided when it was recorded: «يصرف بالكامل» (management may defer it)
        tx.execute("INSERT INTO [CycleDecisions] ([CycleID], [CertificateID], [EntryID], [DecisionType], [RequestedAmount], [ApprovedAmount], [StatusCode], [Reason], [DeferredBefore]) "
                   "VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?)", (cycle_id, e["EntryID"], D_FULL, _r2(e["Amount"]), _r2(e["Amount"]), S_READY, "", 0))
        tx.execute("UPDATE [LedgerEntries] SET [CycleID] = ? WHERE [EntryID] = ?", (cycle_id, e["EntryID"]))


def create(title, cycle_date, user):
    """«إنشاء دورة صرف»: every open certificate of every contractor (and the direct payments recorded and not executed).
    One cycle is open at a time. Returns its id."""
    cur = current_cycle()
    if cur:
        raise CycleError("توجد دورة مفتوحة (رقم %s - %s). أكملها أو احذفها قبل إنشاء دورة جديدة." % (cur["CycleNo"], STAGE_NAME.get(cur["Status"], cur["Status"])))
    rows, entries = candidates(), pending_entries()
    if not rows and not entries:
        raise CycleError("لا توجد مستخلصات مفتوحة ولا دفعات مسجّلة تنتظر الصرف.")
    with db.transaction() as tx:
        no = max([c["CycleNo"] or 0 for c in tx.query("SELECT [CycleNo] FROM [PaymentCycles]")] or [0]) + 1
        cid = tx.execute_insert("INSERT INTO [PaymentCycles] ([CycleNo], [Status], [CreatedAt], [Title], [CycleDate], [CreatedBy]) VALUES (?, ?, ?, ?, ?, ?)",
                               (no, ST_DRAFT, datetime.now(), (title or "")[:150], cycle_date, user))
        _insert_lines(tx, cid, rows, entries)
        _log(tx, {"CycleID": cid, "CycleNo": no}, None, "إنشاء الدورة", "%d مستخلص و%d دفعة مسجّلة" % (len(rows), len(entries)), user)
    return cid


def refresh(cycle_id, user=""):
    """Stage 1 only: re-reads the open certificates and recorded payments (new ones come in, closed or paid ones leave). Notes already written are kept."""
    with db.transaction() as tx:
        c = _cycle(tx, cycle_id)
        _need(c, ST_DRAFT)
        kept = {}
        for d in tx.query("SELECT * FROM [CycleDecisions] WHERE [CycleID] = ?", (cycle_id,)):
            kept[("e", d["EntryID"]) if d.get("EntryID") else ("c", d["CertificateID"])] = d
        for kind, key in kept:
            if kind == "c":
                tx.execute("UPDATE [Certificates] SET [Status] = ?, [CycleID] = NULL WHERE [CertificateID] = ? AND [CycleID] = ?", (ST_ENTERED, key, cycle_id))
            else:
                tx.execute("UPDATE [LedgerEntries] SET [CycleID] = NULL WHERE [EntryID] = ? AND [CycleID] = ?", (key, cycle_id))
        tx.execute("DELETE FROM [CycleDecisions] WHERE [CycleID] = ?", (cycle_id,))
    rows, entries = candidates(exclude_cycle=cycle_id), pending_entries()
    with db.transaction() as tx:
        _insert_lines(tx, cycle_id, rows, entries)
        for d in tx.query("SELECT * FROM [CycleDecisions] WHERE [CycleID] = ?", (cycle_id,)):
            old = kept.get(("e", d["EntryID"]) if d.get("EntryID") else ("c", d["CertificateID"]))
            if old and old.get("NoteText"):
                tx.execute("UPDATE [CycleDecisions] SET [NoteText] = ? WHERE [DecisionID] = ?", (old["NoteText"], d["DecisionID"]))
        _log(tx, c, None, "إعادة احتساب القائمة", "%d مستخلص و%d دفعة مسجّلة" % (len(rows), len(entries)), user)


# ---------------------------------------------------------------- moving between the stages
def to_decision_stage(cycle_id, user=""):
    """«الانتقال للمرحلة التالية»: from the creation sheet to management's decision."""
    with db.transaction() as tx:
        c = _cycle(tx, cycle_id)
        _need(c, ST_DRAFT)
        if not tx.query("SELECT [DecisionID] FROM [CycleDecisions] WHERE [CycleID] = ?", (cycle_id,)):
            raise CycleError("الدورة بلا مستخلصات.")
        tx.execute("UPDATE [PaymentCycles] SET [Status] = ?, [SentToMgmtAt] = ? WHERE [CycleID] = ?", (ST_WAITING, datetime.now(), cycle_id))
        _log(tx, c, None, "الانتقال لمرحلة قرار الإدارة", "", user)


def _payable(d):
    return d["DecisionType"] in (D_FULL, D_PART) and (d.get("ApprovedAmount") or 0) > EPS


def to_execution_stage(cycle_id, user=""):
    """«الانتقال للمرحلة النهائية»: every line has a decision; the cells are closed; only the execution state stays open.
    A deferred certificate (or recorded payment) leaves the cycle now and waits for the next one. A cycle with nothing to transfer closes at once."""
    with db.transaction() as tx:
        c = _cycle(tx, cycle_id)
        _need(c, ST_WAITING)
        lines = tx.query("SELECT * FROM [CycleDecisions] WHERE [CycleID] = ?", (cycle_id,))
        open_ = [d for d in lines if not d.get("DecisionType")]
        if open_:
            raise CycleError("بقيت %d بنود بلا قرار. سجّل قرار الإدارة لكل بند ثم انتقل للمرحلة النهائية." % len(open_))
        for d in lines:
            if d["DecisionType"] == D_DEFER:
                _release(tx, d)
        payable = [d for d in lines if _payable(d)]
        status = ST_APPROVED if payable else ST_DONE
        tx.execute("UPDATE [PaymentCycles] SET [Status] = ?, [ApprovedAt] = ?, [ApprovedBy] = ?, [ClosedAt] = ? WHERE [CycleID] = ?",
                   (status, datetime.now(), user, datetime.now() if status == ST_DONE else None, cycle_id))
        _log(tx, c, None, "الانتقال للمرحلة النهائية (تنفيذ التحويل)" if payable else "إغلاق الدورة: كل البنود مؤجلة", "", user)


def _executed_entries(tx, cycle_id):
    try:
        return tx.query("SELECT [EntryID] FROM [LedgerEntries] WHERE [CycleID] = ? AND [Status] = ?", (cycle_id, ledger.S_DONE))
    except Exception:                                    # noqa
        return []


def go_back(cycle_id, user=""):
    """One stage back, at any time: a closed cycle reopens to the execution stage, execution -> decisions, decisions -> creation."""
    with db.transaction() as tx:
        c = _cycle(tx, cycle_id)
        _need(c, ST_WAITING, ST_APPROVED, ST_DONE)
        if c["Status"] == ST_DONE:
            others = [x for x in tx.query("SELECT [CycleID], [CycleNo], [Status] FROM [PaymentCycles]") if x["CycleID"] != cycle_id and x["Status"] != ST_CANCELLED]
            if any(x["Status"] in OPEN_STATUSES for x in others):
                raise CycleError("توجد دورة أخرى مفتوحة؛ أكملها أو احذفها قبل إعادة فتح هذه الدورة.")
            if any((x["CycleNo"] or 0) > (c["CycleNo"] or 0) for x in others):
                raise CycleError("هذه ليست آخر دورة؛ توجد دورة لاحقة بُنيت على نتيجتها فلا يُعاد فتحها.")
            tx.execute("UPDATE [PaymentCycles] SET [Status] = ?, [ClosedAt] = NULL WHERE [CycleID] = ?", (ST_APPROVED, cycle_id))
            _log(tx, c, None, "إعادة فتح دورة مغلقة", "إلى مرحلة تنفيذ التحويل", user)
        elif c["Status"] == ST_APPROVED:
            if tx.query("SELECT [PaymentID] FROM [Payments] WHERE [CycleID] = ?", (cycle_id,)) or _executed_entries(tx, cycle_id):
                raise CycleError("سُجّلت تحويلات في هذه الدورة؛ غيّر حالة تنفيذ هذه المستخلصات إلى «لم يتم التنفيذ» أولاً.")
            for d in tx.query("SELECT * FROM [CycleDecisions] WHERE [CycleID] = ?", (cycle_id,)):
                if d["DecisionType"] == D_DEFER or d.get("ExecStatus") == EX_NOT:
                    _relock(tx, cycle_id, d)
            tx.execute("UPDATE [CycleDecisions] SET [ExecStatus] = NULL, [ExecAt] = NULL, [ExecBy] = NULL WHERE [CycleID] = ?", (cycle_id,))
            tx.execute("UPDATE [PaymentCycles] SET [Status] = ?, [ApprovedAt] = NULL, [ApprovedBy] = NULL, [ClosedAt] = NULL WHERE [CycleID] = ?", (ST_WAITING, cycle_id))
            _log(tx, c, None, "الرجوع لمرحلة قرار الإدارة", "", user)
        else:
            tx.execute("UPDATE [PaymentCycles] SET [Status] = ? WHERE [CycleID] = ?", (ST_DRAFT, cycle_id))
            _log(tx, c, None, "الرجوع لمرحلة الإنشاء", "القرارات المحفوظة بقيت", user)


def delete_cycle(cycle_id, user=""):
    """Allowed only while no transfer has been recorded in the cycle. Its certificates and recorded payments return to the open lists; the log stays."""
    with db.transaction() as tx:
        c = _cycle(tx, cycle_id)
        if tx.query("SELECT [PaymentID] FROM [Payments] WHERE [CycleID] = ?", (cycle_id,)) or _executed_entries(tx, cycle_id):
            raise CycleError("سُجّلت تحويلات في هذه الدورة فلا تُحذف. غيّر حالة تنفيذها إلى «لم يتم التنفيذ» أولاً إن أردت حذفها.")
        lines = tx.query("SELECT * FROM [CycleDecisions] WHERE [CycleID] = ?", (cycle_id,))
        for d in lines:
            if d.get("EntryID"):
                _release(tx, d)
                continue
            rows = tx.query("SELECT [Status], [CycleID] FROM [Certificates] WHERE [CertificateID] = ?", (d["CertificateID"],))
            if rows and rows[0]["Status"] == ST_IN_CYCLE and rows[0].get("CycleID") == cycle_id:
                _release(tx, d)
        _log(tx, c, None, "حذف الدورة", "دورة رقم %s · %d بند · حالتها «%s»" % (c["CycleNo"], len(lines), c["Status"]), user)
        tx.execute("DELETE FROM [CycleDecisions] WHERE [CycleID] = ?", (cycle_id,))
        tx.execute("DELETE FROM [PaymentCycles] WHERE [CycleID] = ?", (cycle_id,))
        return c["CycleNo"]


def _release(tx, d):
    """The line leaves the cycle: the certificate becomes «مُدخَل» again / the recorded payment waits for the next cycle."""
    if d.get("EntryID"):
        tx.execute("UPDATE [LedgerEntries] SET [CycleID] = NULL WHERE [EntryID] = ? AND [Status] = ?", (d["EntryID"], ledger.S_PENDING))
    else:
        tx.execute("UPDATE [Certificates] SET [Status] = ?, [CycleID] = NULL WHERE [CertificateID] = ?", (ST_ENTERED, d["CertificateID"]))


def _relock(tx, cycle_id, d):
    if d.get("EntryID"):
        rows = tx.query("SELECT [Status], [CycleID] FROM [LedgerEntries] WHERE [EntryID] = ?", (d["EntryID"],))
        if rows and rows[0]["Status"] == ledger.S_PENDING and (not rows[0].get("CycleID") or rows[0]["CycleID"] == cycle_id):
            tx.execute("UPDATE [LedgerEntries] SET [CycleID] = ? WHERE [EntryID] = ?", (cycle_id, d["EntryID"]))
            return
        raise CycleError("الدفعة المسجّلة أُدرجت في دورة أخرى أو نُفِّذت؛ لا يمكن إعادة هذه الدورة للمرحلة السابقة.")
    rows = tx.query("SELECT [Status], [CycleID] FROM [Certificates] WHERE [CertificateID] = ?", (d["CertificateID"],))
    if rows and rows[0]["Status"] == ST_ENTERED and not rows[0].get("CycleID"):
        tx.execute("UPDATE [Certificates] SET [Status] = ?, [CycleID] = ? WHERE [CertificateID] = ?", (ST_IN_CYCLE, cycle_id, d["CertificateID"]))
    else:
        raise CycleError("المستخلص #%s أُدرج في دورة أخرى أو سُدّد؛ لا يمكن إعادة هذه الدورة للمرحلة السابقة." % d["CertificateID"])


# ---------------------------------------------------------------- stages 1 and 2: decisions, amounts, notes (keyed by the line: DecisionID)
def save_lines(cycle_id, entries, user=""):
    """entries: {DecisionID: (decision, partial_amount, note)}.
    Stage 1 saves the notes only; stage 2 saves decision + amount + notes. Every real change is written to the cycle's log.
    Returns the number of lines that carry a decision."""
    with db.transaction() as tx:
        c = _cycle(tx, cycle_id)
        _need(c, ST_DRAFT, ST_WAITING)
        return _save_entries(tx, c, entries, user)


def _label(tx, line):
    if line.get("EntryID"):
        e = tx.query("SELECT [Description] FROM [LedgerEntries] WHERE [EntryID] = ?", (line["EntryID"],))
        return "[دفعة مباشرة: %s] " % (e[0]["Description"] if e else "")
    return ""


def _save_entries(tx, c, entries, user):
    cycle_id = c["CycleID"]
    deciding = c["Status"] == ST_WAITING
    lines = {d["DecisionID"]: d for d in tx.query("SELECT * FROM [CycleDecisions] WHERE [CycleID] = ?", (cycle_id,))}
    for key, (decision, partial, note) in entries.items():
        line = lines.get(int(key))
        if not line:
            raise CycleError("بند ليس في هذه الدورة.")
        cert_id, tag = line.get("CertificateID"), _label(tx, line)
        note = (note or "").strip()[:250]
        if not deciding:
            if note != (line.get("NoteText") or ""):
                tx.execute("UPDATE [CycleDecisions] SET [NoteText] = ? WHERE [DecisionID] = ?", (note, line["DecisionID"]))
                _log(tx, c, cert_id, "ملاحظة", tag + (note or "(حُذفت)"), user)
            continue
        decision = (decision or "").strip()
        if decision and decision not in DECISIONS:
            raise CycleError("قرار غير معروف.")
        if line.get("EntryID") and decision == D_PART:
            raise CycleError("الدفعة المسجّلة تُصرف بالكامل أو تؤجَّل (لا يوجد صرف جزئي لها).")
        requested = _r2(line.get("RequestedAmount"))
        approved = 0.0
        if decision == D_FULL:
            approved = requested
        elif decision == D_PART:
            approved = _r2(partial)
            if not (EPS < approved < requested - EPS):
                raise CycleError("مبلغ الصرف الجزئي للمستخلص #%s يجب أن يكون أكبر من صفر وأقل من المتبقي (%s)." % (cert_id, _money(requested)))
        old_dec = line.get("DecisionType") or ""
        old_amt = _r2(line.get("ApprovedAmount")) if old_dec in (D_FULL, D_PART) else 0.0
        if decision != old_dec or abs(approved - old_amt) > EPS or note != (line.get("NoteText") or ""):
            tx.execute("UPDATE [CycleDecisions] SET [DecisionType] = ?, [PartialAmount] = ?, [ApprovedAmount] = ?, [NoteText] = ?, [DecidedBy] = ?, [DecidedAt] = ? "
                       "WHERE [DecisionID] = ?", (decision, approved if decision == D_PART else None, approved, note, user if decision else None,
                                                  datetime.now() if decision else None, line["DecisionID"]))
            if decision != old_dec or abs(approved - old_amt) > EPS:
                _log(tx, c, cert_id, "قرار الإدارة", "%s%s %s ← %s %s" % (tag, DECISION_LABEL.get(old_dec, "بلا قرار"), _money(old_amt) if old_dec in (D_FULL, D_PART) else "",
                                                                          DECISION_LABEL.get(decision, "بلا قرار"), _money(approved) if decision in (D_FULL, D_PART) else ""), user)
            else:
                _log(tx, c, cert_id, "ملاحظة", tag + (note or "(حُذفت)"), user)
    return sum(1 for d in tx.query("SELECT [DecisionType] FROM [CycleDecisions] WHERE [CycleID] = ?", (cycle_id,)) if d["DecisionType"])


def distribution_plan(lines, amount):
    """[(DecisionID, decision, approved)] for ONE contractor's CERTIFICATES: the approved amount is spread OLDEST first.
    Each is paid in full while the amount lasts, the next one gets what is left (a partial payment), the others are deferred."""
    left, plan = _r2(amount), []
    for l in sorted(lines, key=lambda l: (l["Cert"].get("CertificateDate") or l["Cert"].get("CreatedAt") or datetime.min, l["CertNo"] or 0, l["DecisionID"])):
        req = l["Requested"]
        if req > EPS and left >= req - EPS:
            plan.append((l["DecisionID"], D_FULL, req))
            left = _r2(max(left - req, 0))
        elif left > EPS and req > EPS:
            plan.append((l["DecisionID"], D_PART, left))
            left = 0.0
        else:
            plan.append((l["DecisionID"], D_DEFER, 0.0))
    return plan


def distribute(cycle_id, sub_id, amount, user=""):
    """Management approved ONE amount for a contractor («مبلغ الصرف المعتمد»): it is spread over that contractor's certificates in
    the cycle automatically. It only HELPS - every decision and amount stays editable by hand afterwards. Stage 2 only.
    (A direct payment riding in the cycle is decided on its own line.) Returns [(DecisionID, decision, approved)]."""
    data = load(cycle_id)
    if not data:
        raise CycleError("دورة الصرف غير موجودة.")
    _need(data["cycle"], ST_WAITING)
    mine = [l for l in data["lines"] if l["SubcontractorID"] == int(sub_id) and not l["IsEntry"]]
    if not mine:
        raise CycleError("لا مستخلصات لهذا المقاول في الدورة.")
    total, amount = _r2(sum(l["Requested"] for l in mine)), _r2(amount)
    if amount < 0:
        raise CycleError("مبلغ الصرف المعتمد لا يكون سالبًا.")
    if amount > total + EPS:
        raise CycleError("مبلغ الصرف المعتمد (%s) أكبر من إجمالي المستخلصات المفتوحة لـ %s (%s)." % (_money(amount), mine[0]["SubName"], _money(total)))
    plan = distribution_plan(mine, amount)
    by_id = {l["DecisionID"]: l for l in mine}
    entries = {did: (dec, appr, by_id[did]["Note"]) for did, dec, appr in plan}
    with db.transaction() as tx:
        c = _cycle(tx, cycle_id)
        _need(c, ST_WAITING)
        _save_entries(tx, c, entries, user)
        _log(tx, c, None, "توزيع مبلغ الصرف المعتمد", "%s: %s من %s على %d مستخلص" % (mine[0]["SubName"], _money(amount), _money(total), len(mine)), user)
    return plan


# ---------------------------------------------------------------- stage 3: the execution of the transfers (ONE transfer per contractor)
def _sync_cycle_status(tx, cycle_id):
    """The cycle closes when every line that was approved for payment has an outcome; it is open again when one has not."""
    lines = tx.query("SELECT * FROM [CycleDecisions] WHERE [CycleID] = ?", (cycle_id,))
    pending = [d for d in lines if _payable(d) and not d.get("ExecStatus")]
    if pending:
        tx.execute("UPDATE [PaymentCycles] SET [Status] = ?, [ClosedAt] = NULL WHERE [CycleID] = ?", (ST_APPROVED, cycle_id))
    else:
        tx.execute("UPDATE [PaymentCycles] SET [Status] = ?, [ClosedAt] = ? WHERE [CycleID] = ?", (ST_DONE, datetime.now(), cycle_id))


def _certificate_after(tx, cycle_id, cert_id, paid_at=None):
    """Status of the certificate after its transfer in this cycle was added, removed or cancelled."""
    g = tx.query("SELECT * FROM [Certificates] WHERE [CertificateID] = ?", (cert_id,))[0]
    total = row_amounts(g)[3]
    sent_all = _r2(sum(p["Amount"] or 0 for p in tx.query("SELECT [Amount] FROM [Payments] WHERE [CertificateID] = ?", (cert_id,))))
    if sent_all >= total - EPS and sent_all > 0:
        tx.execute("UPDATE [Certificates] SET [Status] = ?, [CycleID] = NULL, [PaymentDate] = ? WHERE [CertificateID] = ?", (ST_PAID, paid_at or datetime.now(), cert_id))
    else:                                                # the rest (or all of it) is still owed: it waits for the next cycle
        tx.execute("UPDATE [Certificates] SET [Status] = ?, [CycleID] = NULL, [PaymentDate] = NULL WHERE [CertificateID] = ?", (ST_ENTERED, cert_id))


def _apply_exec(tx, cycle_id, d, state, paid_at, bank_ref, note, user, doc_id, wording, pay_no=None):
    """The outcome of ONE line (a certificate or a recorded direct payment)."""
    if d.get("EntryID"):
        if state == EX_DONE:
            tx.execute("UPDATE [LedgerEntries] SET [Status] = ?, [EntryDate] = ?, [ExecAt] = ?, [BankRef] = ?, [PayNo] = ?, [ArchiveFolder] = NULL WHERE [EntryID] = ?",
                       (ledger.S_DONE, paid_at or datetime.now(), datetime.now(), (bank_ref or "")[:80], pay_no, d["EntryID"]))
        else:                                            # not executed: the recorded payment waits for the next cycle
            tx.execute("UPDATE [LedgerEntries] SET [Status] = ?, [CycleID] = NULL, [ExecAt] = NULL, [PayNo] = NULL WHERE [EntryID] = ?", (ledger.S_PENDING, d["EntryID"]))
    else:
        cert_id, approved = d["CertificateID"], _r2(d.get("ApprovedAmount"))
        tx.execute("DELETE FROM [Payments] WHERE [CycleID] = ? AND [CertificateID] = ?", (cycle_id, cert_id))
        if state == EX_DONE:
            crow = tx.query("SELECT [ContractID], [SubcontractorID] FROM [Certificates] WHERE [CertificateID] = ?", (cert_id,))
            sub_id = (crow[0].get("SubcontractorID") if crow else None) or ((tx.query("SELECT [SubcontractorID] FROM [Contracts] WHERE [ContractID] = ?", (crow[0]["ContractID"],)) or [{}])[0].get("SubcontractorID") if crow and crow[0].get("ContractID") else None)
            tx.execute("INSERT INTO [Payments] ([CertificateID], [SubcontractorID], [PaidAt], [Amount], [BankRef], [CycleID], [DocID], [Note], [Statement], [PayNo], [CreatedBy], [CreatedAt]) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (cert_id, sub_id, paid_at or datetime.now(), approved, (bank_ref or "")[:80], cycle_id,
                                                                      (doc_id or {}).get(cert_id) if isinstance(doc_id, dict) else doc_id, (note or "")[:250], (wording or "")[:250], pay_no, user, datetime.now()))
        _certificate_after(tx, cycle_id, cert_id, paid_at)
    tx.execute("UPDATE [CycleDecisions] SET [ExecStatus] = ?, [ExecAt] = ?, [ExecBy] = ? WHERE [DecisionID] = ?", (state, datetime.now(), user, d["DecisionID"]))


def _group(data, sub_id):
    return next((gp for gp in data["groups"] if gp["sub_id"] == int(sub_id)), None)


def set_group_execution(cycle_id, sub_id, state, paid_at, bank_ref, note, user, doc_id=None):
    """The outcome of the contractor's transfer - ONE transfer covers all its payable lines (certificates and recorded payments).
    «تم التحويل»: each certificate gets its payment (with the wording of the real case), recorded payments become executed.
    «لم يتم التنفيذ»: nothing is paid, the certificates go to the next cycle, recorded payments wait for the next cycle.
    doc_id: None, one id for all, or {certificate_id: id}. Returns the cycle's status."""
    if state not in EXEC_STATES:
        raise CycleError("اختر «تم التحويل» أو «لم يتم التنفيذ».")
    data = load(cycle_id)
    if not data:
        raise CycleError("دورة الصرف غير موجودة.")
    gp = _group(data, sub_id)
    wording = gp["statement"] if gp else ""
    with db.transaction() as tx:
        c = _cycle(tx, cycle_id)
        _need(c, ST_APPROVED)
        mine = [l for l in (gp or {"lines": []})["lines"] if l["Payable"]]
        if not mine:
            raise CycleError("لا مبلغ معتمد للصرف لهذا المقاول في الدورة.")
        decisions = {d["DecisionID"]: d for d in tx.query("SELECT * FROM [CycleDecisions] WHERE [CycleID] = ?", (cycle_id,))}
        pay_no = ledger.next_pay_no(int(sub_id), tx) if state == EX_DONE else None        # ONE transfer = ONE payment number for the contractor
        for l in mine:
            _apply_exec(tx, cycle_id, decisions[l["DecisionID"]], state, paid_at, bank_ref, note, user, doc_id, wording, pay_no)
        amount = sum(l["Approved"] for l in mine)
        _log(tx, c, None, "تنفيذ تحويل المقاول", "%s · %s · %s%s%s" % (gp["name"], state, _money(amount), (" · مرجع " + bank_ref) if bank_ref else "", (" · " + wording) if wording and state == EX_DONE else ""), user)
        _sync_cycle_status(tx, cycle_id)
        return tx.query("SELECT [Status] FROM [PaymentCycles] WHERE [CycleID] = ?", (cycle_id,))[0]["Status"]


def clear_group_execution(cycle_id, sub_id, user=""):
    """Takes the contractor's transfer back to «not decided yet» (a wrongly chosen outcome): its payments are removed."""
    data = load(cycle_id)
    gp = _group(data, sub_id) if data else None
    with db.transaction() as tx:
        c = _cycle(tx, cycle_id)
        _need(c, ST_APPROVED)
        mine = [l for l in (gp or {"lines": []})["lines"] if l["Payable"] and l["Exec"]]
        if not mine:
            raise CycleError("لا حالة تنفيذ مسجّلة لهذا المقاول.")
        for l in mine:
            if l["IsEntry"]:
                tx.execute("UPDATE [LedgerEntries] SET [Status] = ?, [CycleID] = ?, [ExecAt] = NULL, [PayNo] = NULL, [ArchiveFolder] = NULL WHERE [EntryID] = ?", (ledger.S_PENDING, cycle_id, l["EntryID"]))
            else:
                tx.execute("DELETE FROM [Payments] WHERE [CycleID] = ? AND [CertificateID] = ?", (cycle_id, l["CertificateID"]))
                tx.execute("UPDATE [Certificates] SET [Status] = ?, [CycleID] = ?, [PaymentDate] = NULL WHERE [CertificateID] = ?", (ST_IN_CYCLE, cycle_id, l["CertificateID"]))
            tx.execute("UPDATE [CycleDecisions] SET [ExecStatus] = NULL, [ExecAt] = NULL, [ExecBy] = NULL WHERE [DecisionID] = ?", (l["DecisionID"],))
        _log(tx, c, None, "إلغاء حالة تنفيذ المقاول", "%s · كانت «%s»" % (gp["name"], mine[0]["Exec"]), user)
        _sync_cycle_status(tx, cycle_id)


def set_execution(cycle_id, cert_id, state, paid_at, bank_ref, note, user, doc_id=None):
    """The outcome of ONE certificate line (kept for corrections of a single line)."""
    cert_id = int(cert_id)
    if state not in EXEC_STATES:
        raise CycleError("اختر «تم التحويل» أو «لم يتم التنفيذ».")
    data = load(cycle_id)
    gp = next((g for g in (data or {"groups": []})["groups"] if any(l["CertificateID"] == cert_id for l in g["lines"])), None)
    with db.transaction() as tx:
        c = _cycle(tx, cycle_id)
        _need(c, ST_APPROVED)
        line = next((d for d in tx.query("SELECT * FROM [CycleDecisions] WHERE [CycleID] = ?", (cycle_id,)) if d["CertificateID"] == cert_id), None)
        if not line or line.get("DecisionType") not in (D_FULL, D_PART):
            raise CycleError("لا قرار صرف لهذا المستخلص في الدورة.")
        if _r2(line.get("ApprovedAmount")) <= EPS:
            raise CycleError("مبلغ الصرف المعتمد لهذا المستخلص صفر.")
        sub_of = next((g_["sub_id"] for g_ in (data or {"groups": []})["groups"] if any(l["CertificateID"] == cert_id for l in g_["lines"])), None)
        _apply_exec(tx, cycle_id, line, state, paid_at, bank_ref, note, user, doc_id, gp["statement"] if gp else "", ledger.next_pay_no(sub_of, tx) if (state == EX_DONE and sub_of) else None)
        _sync_cycle_status(tx, cycle_id)
        _log(tx, c, cert_id, "تنفيذ التحويل", "%s · %s%s" % (state, _money(line.get("ApprovedAmount")), (" · مرجع " + bank_ref) if bank_ref else ""), user)
        return tx.query("SELECT [Status] FROM [PaymentCycles] WHERE [CycleID] = ?", (cycle_id,))[0]["Status"]


def clear_execution(cycle_id, cert_id, user=""):
    """Takes ONE certificate line back to «not decided yet»."""
    cert_id = int(cert_id)
    with db.transaction() as tx:
        c = _cycle(tx, cycle_id)
        _need(c, ST_APPROVED)
        line = next((d for d in tx.query("SELECT * FROM [CycleDecisions] WHERE [CycleID] = ?", (cycle_id,)) if d["CertificateID"] == cert_id), None)
        if not line or not line.get("ExecStatus"):
            raise CycleError("لا حالة تنفيذ مسجّلة لهذا المستخلص.")
        tx.execute("DELETE FROM [Payments] WHERE [CycleID] = ? AND [CertificateID] = ?", (cycle_id, cert_id))
        tx.execute("UPDATE [Certificates] SET [Status] = ?, [CycleID] = ?, [PaymentDate] = NULL WHERE [CertificateID] = ?", (ST_IN_CYCLE, cycle_id, cert_id))
        tx.execute("UPDATE [CycleDecisions] SET [ExecStatus] = NULL, [ExecAt] = NULL, [ExecBy] = NULL WHERE [DecisionID] = ?", (line["DecisionID"],))
        _sync_cycle_status(tx, cycle_id)
        _log(tx, c, cert_id, "إلغاء حالة التنفيذ", "كانت «%s»" % line["ExecStatus"], user)


def group_pay_no(cycle_id, sub_id):
    """The payment number of the contractor's executed transfer in the cycle (None when it was not executed)."""
    for r in db.query("SELECT * FROM [Payments] WHERE [CycleID] = ?", (cycle_id,)):
        if r.get("SubcontractorID") == int(sub_id) and r.get("PayNo"):
            return r["PayNo"]
    for e in _entry_rows().values():
        if e.get("CycleID") == cycle_id and e["SubcontractorID"] == int(sub_id) and e.get("PayNo") and e["Status"] == ledger.S_DONE:
            return e["PayNo"]
    return None

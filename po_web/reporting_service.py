# -*- coding: utf-8 -*-
"""
reporting_service.py - the ONE place the management reports read their numbers from:
the subcontractors dashboard, the project analysis and the contractor status report.

Nothing is copied: everything is computed from the same tables the contractor's account statement reads, so the numbers of a
dashboard always agree with the statements:

    debit  (مدين)  = the net due of every certificate + the debit entries (رد خصم، رد ضريبة ...)
    credit (دائن)  = every executed transfer paid on a certificate + the credit entries (دفعة مباشرة، مواد ...)
    remaining      = debit - credit          (the statement balance, with the sign turned: positive = still owed to the contractor)

Period rule: certificates and payments are counted INSIDE the period; the remaining is the balance up to the END of the period.
"""
from datetime import datetime, timedelta

import contracts_service as cs
import db
import items_service
import ledger_service as ls
from finance import ST_ENTERED, ST_IN_CYCLE, ST_PAID, ST_REJECTED
from textnorm import norm_key

EPS = 0.005
AR_MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
NO_PROJECT = "غير موزّعة على مشروع"


def _r2(x):
    return round(float(x or 0), 2)


def _month_key(d):
    return (d.year, d.month)


def month_label(k):
    return "%s %d" % (AR_MONTHS[k[1] - 1], k[0])


def _next_month(k):
    return (k[0] + (k[1] // 12), (k[1] % 12) + 1)


def months_between(first, last, limit=24):
    out, k = [], first
    while k <= last and len(out) < 120:
        out.append(k)
        k = _next_month(k)
    return out[-limit:]


class Scope:
    """project / site / contractor / period filters of a report."""

    def __init__(self, project="", site="", sub_id=None, date_from=None, date_to=None):
        self.project, self.site, self.sub_id, self.date_from, self.date_to = (project or "").strip(), (site or "").strip(), sub_id, date_from, date_to
        self.pk, self.sk = norm_key(self.project), norm_key(self.site)

    def hit(self, project, site, sub_id):
        return ((not self.pk or norm_key(project) == self.pk) and (not self.sk or norm_key(site) == self.sk) and (not self.sub_id or sub_id == self.sub_id))

    def in_period(self, d):
        if not d:
            return not (self.date_from or self.date_to)
        return (not self.date_from or d >= self.date_from) and (not self.date_to or d < self.date_to + timedelta(days=1))

    def until_end(self, d):
        """For the cumulative balance: everything up to the END of the period (no lower bound)."""
        return not self.date_to or not d or d < self.date_to + timedelta(days=1)

    @property
    def label(self):
        parts = []
        if self.project:
            parts.append("مشروع: " + self.project)
        if self.site:
            parts.append("موقع: " + self.site)
        if self.date_from or self.date_to:
            parts.append("الفترة: %s إلى %s" % (self.date_from.strftime("%Y-%m-%d") if self.date_from else "البداية", self.date_to.strftime("%Y-%m-%d") if self.date_to else "اليوم"))
        return " · ".join(parts)


class Context:
    """Everything the reports need, read once."""

    def __init__(self):
        self.subs, self.contracts, self.certs, self.pays = cs.load()
        self.contracts = {k: c for k, c in self.contracts.items() if cs.counts(c)}
        self.orders = list(items_service._orders().values())
        try:
            self.entries = [e for e in db.query("SELECT * FROM [LedgerEntries]") if e["Status"] == ls.S_DONE]
            self.pending_entries = [e for e in db.query("SELECT * FROM [LedgerEntries]") if e["Status"] != ls.S_DONE]
        except Exception:                                # noqa - before the migration
            self.entries, self.pending_entries = [], []
        try:
            self.contract_sites = {}
            for r in db.query("SELECT [ContractID], [SiteName] FROM [ContractSites]"):
                self.contract_sites.setdefault(r["ContractID"], set()).add(r["SiteName"])
        except Exception:                                # noqa
            self.contract_sites = {}
        self.cycles = {c["CycleID"]: c for c in db.query("SELECT * FROM [PaymentCycles]")}
        self.decisions = db.query("SELECT * FROM [CycleDecisions]")
        self.by_cert = {g["CertificateID"]: g for g in self.certs}
        self.facts = self._facts()

    def _facts(self):
        """One flat list of movements: kind, date, sub_id, project, site, debit, credit, ref."""
        out = []
        for g in self.certs:
            if g["Status"] == ST_REJECTED:
                continue
            out.append({"kind": "cert", "date": g.get("CertificateDate") or g.get("CreatedAt"), "sub_id": g["SubcontractorID"], "project": g.get("Project") or "",
                        "site": g.get("SiteName") or "", "debit": _r2(g["Total"]), "credit": 0.0, "ref": g["CertificateID"], "cert": g})
        paid_certs = {p["CertificateID"] for p in self.pays}
        for p in self.pays:
            g = self.by_cert.get(p["CertificateID"])
            if not g:
                continue
            out.append({"kind": "pay", "date": p.get("PaidAt"), "sub_id": g["SubcontractorID"], "project": g.get("Project") or "", "site": g.get("SiteName") or "",
                        "debit": 0.0, "credit": _r2(p["Amount"]), "ref": p["PaymentID"], "cert": g, "pay": p})
        for g in self.certs:                              # paid in an older version: no payment record, the money was paid
            if g["Status"] == ST_PAID and g["CertificateID"] not in paid_certs:
                out.append({"kind": "pay", "date": g.get("PaymentDate") or g.get("CertificateDate") or g.get("CreatedAt"), "sub_id": g["SubcontractorID"], "project": g.get("Project") or "",
                            "site": g.get("SiteName") or "", "debit": 0.0, "credit": _r2(g["Total"]), "ref": -g["CertificateID"], "cert": g, "pay": None})
        for e in self.entries:
            out.append({"kind": "entry", "date": e.get("EntryDate"), "sub_id": e["SubcontractorID"], "project": e.get("ProjectName") or "", "site": e.get("SiteName") or "",
                        "debit": _r2(e["Amount"]) if e["Kind"] == ls.K_DEBIT else 0.0, "credit": _r2(e["Amount"]) if e["Kind"] == ls.K_CREDIT else 0.0, "ref": e["EntryID"], "entry": e})
        return out

    def sub_name(self, sub_id):
        return (self.subs.get(sub_id) or {}).get("Name", "")


def _contract_hit(ctx, c, scope):
    if scope.sub_id and c.get("SubcontractorID") != scope.sub_id:
        return False
    if scope.pk and norm_key(c.get("Project")) != scope.pk:
        return False
    if scope.sk:
        names = {norm_key(c.get("SiteName"))} | {norm_key(x) for x in ctx.contract_sites.get(c["ContractID"], set())}
        if scope.sk not in names:
            return False
    return True


def _last_decision_deferred(ctx):
    """{certificate_id} whose MOST RECENT cycle decision was «تأجيل»."""
    last = {}
    for d in ctx.decisions:
        if not d.get("CertificateID") or (ctx.cycles.get(d["CycleID"]) or {}).get("Status") == "ملغاة":
            continue
        if d["CertificateID"] not in last or d["DecisionID"] > last[d["CertificateID"]]["DecisionID"]:
            last[d["CertificateID"]] = d
    return {cid for cid, d in last.items() if d.get("DecisionType") == "تأجيل"}


def _by_month(values, scope, first_last):
    """{(y, m): value} -> list over the months of the window."""
    first, last = first_last
    return [round(values.get(k, 0), 2) for k in months_between(first, last)]


def _window(scope, dates):
    """(first month, last month) of a chart: the period when one is given, otherwise the last 12 months of activity."""
    if scope.date_from or scope.date_to:
        return _month_key(scope.date_from or min(dates or [datetime.now()])), _month_key(scope.date_to or max(dates or [datetime.now()]))
    last = _month_key(max(dates)) if dates else _month_key(datetime.now())
    idx = last[0] * 12 + last[1] - 1 - 11
    return (idx // 12, idx % 12 + 1), last


# ---------------------------------------------------------------- the subcontractors dashboard
def dashboard(ctx, scope):
    F = [f for f in ctx.facts if scope.hit(f["project"], f["site"], f["sub_id"])]
    P = [f for f in F if scope.in_period(f["date"])]
    cum = [f for f in F if scope.until_end(f["date"])]
    certs_in = [g for g in ctx.certs if g["Status"] != ST_REJECTED and scope.hit(g.get("Project") or "", g.get("SiteName") or "", g["SubcontractorID"]) and scope.in_period(g.get("CertificateDate") or g.get("CreatedAt"))]
    deferred_ids = _last_decision_deferred(ctx)
    open_ = [g for g in certs_in if g["Status"] in (ST_ENTERED, ST_IN_CYCLE) and g["Remaining"] > EPS and not g.get("ClosedAt")]
    contracts = [c for c in ctx.contracts.values() if _contract_hit(ctx, c, scope)]
    sub_ids = {c["SubcontractorID"] for c in contracts} | {f["sub_id"] for f in F}
    if not (scope.pk or scope.sk or scope.sub_id):
        sub_ids = set(ctx.subs)
    debit_p, credit_p = _r2(sum(f["debit"] for f in P)), _r2(sum(f["credit"] for f in P))
    remaining = _r2(sum(f["debit"] - f["credit"] for f in cum))
    kpi = {"contracts_value": _r2(sum(float(c.get("ContractValue") or 0) for c in contracts)), "certs_total": _r2(sum(f["debit"] for f in P if f["kind"] == "cert")),
           "paid": credit_p, "remaining": remaining, "current_dues": _r2(sum(g["Remaining"] for g in open_)), "contractors": len(sub_ids),
           "active_contracts": sum(1 for c in contracts if c.get("Status") == "ساري"), "open_certs": len(open_), "paid_certs": sum(1 for g in certs_in if g["Status"] == ST_PAID),
           "deferred_certs": sum(1 for g in open_ if g["CertificateID"] in deferred_ids), "in_cycle_certs": sum(1 for g in certs_in if g["Status"] == ST_IN_CYCLE),
           "dues": debit_p, "paid_ratio": round(credit_p / debit_p * 100, 1) if debit_p > EPS else 0.0}
    per_sub = {}
    for f in P:
        r = per_sub.setdefault(f["sub_id"], {"sub_id": f["sub_id"], "name": ctx.sub_name(f["sub_id"]), "dues": 0.0, "paid": 0.0, "remaining": 0.0})
        r["dues"] += f["debit"]
        r["paid"] += f["credit"]
    for f in cum:
        r = per_sub.setdefault(f["sub_id"], {"sub_id": f["sub_id"], "name": ctx.sub_name(f["sub_id"]), "dues": 0.0, "paid": 0.0, "remaining": 0.0})
        r["remaining"] += f["debit"] - f["credit"]
    subs_rows = sorted((dict(r, dues=_r2(r["dues"]), paid=_r2(r["paid"]), remaining=_r2(r["remaining"])) for r in per_sub.values() if r["name"]), key=lambda r: -r["dues"])
    per_project = {}
    for f in P:
        key = f["project"] or NO_PROJECT
        r = per_project.setdefault(key, {"name": key, "dues": 0.0, "paid": 0.0, "certs": 0})
        r["dues"] += f["debit"]
        r["paid"] += f["credit"]
        r["certs"] += 1 if f["kind"] == "cert" else 0
    proj_rows = sorted((dict(r, dues=_r2(r["dues"]), paid=_r2(r["paid"])) for r in per_project.values()), key=lambda r: -r["dues"])
    dates = [f["date"] for f in F if f["date"]]
    window = _window(scope, dates)
    mp, mc, mv = {}, {}, {}
    for f in P:
        if not f["date"]:
            continue
        k = _month_key(f["date"])
        if f["credit"]:
            mp[k] = mp.get(k, 0) + f["credit"]
        if f["kind"] == "cert":
            mc[k] = mc.get(k, 0) + 1
            mv[k] = mv.get(k, 0) + f["debit"]
    months = months_between(*window)
    monthly = {"labels": [month_label(k) for k in months], "paid": _by_month(mp, scope, window), "cert_count": [int(mc.get(k, 0)) for k in months], "cert_value": _by_month(mv, scope, window)}
    return {"kpi": kpi, "subs": subs_rows, "projects": proj_rows, "monthly": monthly, "scope": scope}


# ---------------------------------------------------------------- the project analysis
def projects(ctx):
    names = {}
    for g in ctx.certs:
        if g.get("Project"):
            names[norm_key(g["Project"])] = g["Project"]
    for c in ctx.contracts.values():
        if c.get("Project"):
            names.setdefault(norm_key(c["Project"]), c["Project"])
    for o in ctx.orders:
        names.setdefault(norm_key(o["proj"]), o["proj"])
    return sorted(names.values(), key=norm_key)


def project_analysis(ctx, project, date_from=None, date_to=None):
    scope = Scope(project=project, date_from=date_from, date_to=date_to)
    pos = [o for o in ctx.orders if norm_key(o["proj"]) == scope.pk and scope.in_period(o.get("OrderDate") or o.get("CreatedAt"))]
    po_paid_rows = [o for o in pos if o.get("PaymentStatus") == "تم السداد"]
    F = [f for f in ctx.facts if scope.hit(f["project"], f["site"], f["sub_id"])]
    P = [f for f in F if scope.in_period(f["date"])]
    cum = [f for f in F if scope.until_end(f["date"])]
    contracts = [c for c in ctx.contracts.values() if _contract_hit(ctx, c, scope)]
    certs = [g for g in ctx.certs if g["Status"] != ST_REJECTED and scope.hit(g.get("Project") or "", g.get("SiteName") or "", g["SubcontractorID"]) and scope.in_period(g.get("CertificateDate") or g.get("CreatedAt"))]
    sub_ids = {c["SubcontractorID"] for c in contracts} | {g["SubcontractorID"] for g in certs}
    sup = {o.get("SupplierName") for o in pos if o.get("SupplierName")}
    po_total = _r2(sum(o.get("TotalAmount") or 0 for o in pos))
    po_paid = _r2(sum(o.get("TotalAmount") or 0 for o in po_paid_rows))
    sub_paid = _r2(sum(f["credit"] for f in P))
    sub_debit = _r2(sum(f["debit"] for f in P))
    certs_total = _r2(sum(g["Total"] for g in certs))
    sub_remaining = _r2(sum(f["debit"] - f["credit"] for f in cum))
    kpi = {"po_total": po_total, "contracts_value": _r2(sum(float(c.get("ContractValue") or 0) for c in contracts)), "certs_total": certs_total,
           "sub_paid": sub_paid, "po_paid": po_paid, "paid_total": _r2(sub_paid + po_paid), "sub_remaining": sub_remaining, "po_remaining": _r2(po_total - po_paid),
           "remaining_total": _r2(sub_remaining + po_total - po_paid), "suppliers": len(sup), "contractors": len(sub_ids), "contracts": len(contracts), "certs": len(certs), "orders": len(pos),
           "manual_debit": _r2(sum(f["debit"] for f in P if f["kind"] == "entry"))}
    # tables
    po_rows = sorted(({"po": o["PONumber"], "date": o.get("OrderDate"), "supplier": o.get("SupplierName") or "", "site": o["site"], "total": _r2(o.get("TotalAmount")),
                       "paid": o.get("PaymentStatus") == "تم السداد"} for o in pos), key=lambda r: r["date"] or datetime.min, reverse=True)
    contract_rows = [{"no": c.get("ContractNo") or ("#%s" % c["ContractID"]), "sub": ctx.sub_name(c["SubcontractorID"]), "site": c.get("SiteName") or "", "value": _r2(c.get("ContractValue")), "status": c.get("Status") or ""}
                     for c in sorted(contracts, key=lambda c: c["ContractID"])]
    cert_rows = sorted(({"no": g["CertificateNo"], "sub": g["SubcontractorName"], "site": g.get("SiteName") or "", "date": g.get("CertificateDate") or g.get("CreatedAt"), "total": _r2(g["Total"]),
                         "paid": _r2(g["Paid"]), "remaining": _r2(g["Remaining"]), "status": g["Status"]} for g in certs), key=lambda r: r["date"] or datetime.min, reverse=True)
    pay_rows = sorted(({"date": f["date"], "sub": ctx.sub_name(f["sub_id"]), "amount": f["credit"], "site": f["site"],
                        "no": (f.get("pay") or {}).get("PayNo") if f["kind"] == "pay" else (f["entry"].get("PayNo") if f.get("entry") else None),
                        "desc": ((f.get("pay") or {}).get("Statement") or ("مستخلص رقم %s" % f["cert"]["CertificateNo"])) if f["kind"] == "pay" else "%s — %s" % (f["entry"]["Category"], f["entry"]["Description"])}
                       for f in P if f["credit"] > 0), key=lambda r: r["date"] or datetime.min, reverse=True)
    # charts
    dates = [f["date"] for f in F if f["date"]] + [o.get("PaymentDate") or o.get("OrderDate") for o in po_paid_rows if (o.get("PaymentDate") or o.get("OrderDate"))]
    window = _window(scope, dates)
    ms, mpo = {}, {}
    for f in P:
        if f["credit"] and f["date"]:
            ms[_month_key(f["date"])] = ms.get(_month_key(f["date"]), 0) + f["credit"]
    for o in po_paid_rows:
        d = o.get("PaymentDate") or o.get("OrderDate")
        if d:
            mpo[_month_key(d)] = mpo.get(_month_key(d), 0) + (o.get("TotalAmount") or 0)
    months = months_between(*window)
    monthly = {"labels": [month_label(k) for k in months], "subs": _by_month(ms, scope, window), "pos": _by_month(mpo, scope, window)}
    top_c, top_s = {}, {}
    for g in certs:
        top_c[g["SubcontractorName"]] = top_c.get(g["SubcontractorName"], 0) + g["Total"]
    for o in pos:
        top_s[o.get("SupplierName") or "-"] = top_s.get(o.get("SupplierName") or "-", 0) + (o.get("TotalAmount") or 0)
    cost = [{"label": "أوامر الشراء", "value": po_total}, {"label": "مستخلصات المقاولين", "value": certs_total}, {"label": "استحقاقات أخرى (حركات يدوية)", "value": kpi["manual_debit"]}]
    return {"kpi": kpi, "po_rows": po_rows, "contract_rows": contract_rows, "cert_rows": cert_rows, "pay_rows": pay_rows, "monthly": monthly,
            "top_contractors": sorted(({"label": k, "value": _r2(v)} for k, v in top_c.items()), key=lambda r: -r["value"])[:10],
            "top_suppliers": sorted(({"label": k, "value": _r2(v)} for k, v in top_s.items()), key=lambda r: -r["value"])[:10],
            "cost": [c for c in cost if c["value"] > 0], "scope": scope}


# ---------------------------------------------------------------- the contractor status report
def contractor_report(ctx, sub_id):
    sub = ctx.subs.get(sub_id)
    if not sub:
        return None
    fig = cs.sub_figures(sub_id, ctx.contracts, list(ctx.certs))
    st = ls.statement(sub_id)
    mine_contracts = sorted((c for c in ctx.contracts.values() if c.get("SubcontractorID") == sub_id), key=lambda c: c["ContractID"])
    mine_certs = sorted((g for g in ctx.certs if g["SubcontractorID"] == sub_id), key=lambda g: ((g.get("Project") or ""), (g.get("SiteName") or ""), g["CertificateNo"] or 0))
    cert_ids = {g["CertificateID"] for g in mine_certs}
    cyc = {}
    for d in ctx.decisions:
        mine = (d.get("CertificateID") in cert_ids) or (d.get("EntryID") and any(e["EntryID"] == d["EntryID"] and e["SubcontractorID"] == sub_id for e in ctx.entries + ctx.pending_entries))
        if mine and (ctx.cycles.get(d["CycleID"]) or {}).get("Status") != "ملغاة":
            cyc.setdefault(d["CycleID"], 0.0)
    paid_cycle_rows = [p for p in ctx.pays if p["CertificateID"] in cert_ids and p.get("CycleID")]
    cycle_paid = {}
    for p in paid_cycle_rows:
        cycle_paid[p["CycleID"]] = cycle_paid.get(p["CycleID"], 0) + (p["Amount"] or 0)
    for e in ctx.entries:
        if e["SubcontractorID"] == sub_id and e.get("CycleID"):
            cycle_paid[e["CycleID"]] = cycle_paid.get(e["CycleID"], 0) + (e["Amount"] or 0)
    last = max(cycle_paid, key=lambda cid: (ctx.cycles.get(cid) or {}).get("CycleNo") or 0) if cycle_paid else None
    docs = [d for d in db.query("SELECT * FROM [Documents]") if d.get("IsCurrent")]
    cids = {str(c["ContractID"]) for c in mine_contracts}
    eids = {str(e["EntryID"]) for e in ctx.entries + ctx.pending_entries if e["SubcontractorID"] == sub_id}
    mine_docs = [d for d in docs if (d["EntityType"] == "SUB" and str(d["EntityKey"]) == str(sub_id)) or (d["EntityType"] == "CONTRACT" and str(d["EntityKey"]) in cids)
                 or (d["EntityType"] == "CERT" and str(d["EntityKey"]) in {str(i) for i in cert_ids}) or (d["EntityType"] == "LEDGER" and str(d["EntityKey"]) in eids)
                 or (d["EntityType"] == "PAYMENT" and str(d["EntityKey"]).split(":")[0] == str(sub_id))]
    docs_of = lambda kind, ids: {str(d["EntityKey"]) for d in mine_docs if d["EntityType"] == kind and str(d["EntityKey"]) in ids}
    archive = {"files": len(mine_docs), "contracts_with_files": len(docs_of("CONTRACT", cids)), "contracts": len(mine_contracts),
               "certs_with_files": len(docs_of("CERT", {str(i) for i in cert_ids})), "certs": len(mine_certs), "closed_transactions": sum(1 for g in mine_certs if g.get("ClosedAt"))}
    return {"sub": sub, "fig": fig, "statement": {"debit": st["debit"], "credit": st["credit"], "balance": st["balance"], "note": st["note"]},
            "contracts": [{"no": c.get("ContractNo") or ("#%s" % c["ContractID"]), "project": c.get("Project") or "", "site": c.get("SiteName") or "", "value": _r2(c.get("ContractValue")), "status": c.get("Status") or ""} for c in mine_contracts],
            "certs": [{"no": g["CertificateNo"], "project": g.get("Project") or "", "site": g.get("SiteName") or "", "total": _r2(g["Total"]), "paid": _r2(g["Paid"]), "remaining": _r2(g["Remaining"]), "status": g["Status"]} for g in mine_certs],
            "cycles": {"count": len(cyc), "paid": _r2(sum(cycle_paid.values())), "last_no": (ctx.cycles[last]["CycleNo"] if last else None), "last_date": ((ctx.cycles[last].get("CycleDate") or ctx.cycles[last].get("CreatedAt")) if last else None),
                       "last_paid": _r2(cycle_paid.get(last, 0)) if last else 0.0},
            "archive": archive, "generated": datetime.now()}

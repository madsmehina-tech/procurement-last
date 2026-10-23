# -*- coding: utf-8 -*-
"""
contracts_service.py - every number about subcontractors, contracts, certificates and payments, computed in ONE place.

The contracts tab, the certificates tab, the payments tab, the subcontractor file and the contract page used to each
add up certificates their own way; now they all read the same figures from here, so they can never disagree.
  executed   = gross work of the certificates that are not rejected
  due        = total (net + VAT) of the certificates that are not rejected
  paid       = sum of the recorded payments
  remaining  = due - paid
"""
from datetime import datetime

import db
import money_guards as mg
from finance import ST_PAID, ST_REJECTED, certificate_value, classify_status, norm_status, row_amounts


def _payments():
    try:
        return db.query("SELECT * FROM [Payments]")
    except Exception as e:                               # only «the table does not exist yet» (an old database) is tolerated; any other error must not turn «paid» into zero
        if not mg.is_missing_table(e):
            raise
        return []


def load():
    subs = {s["SubcontractorID"]: s for s in db.query("SELECT * FROM [Subcontractors]")}
    contracts = {c["ContractID"]: c for c in db.query("SELECT * FROM [Contracts]")}
    pays = _payments()
    try:
        cycle_no = {x["CycleID"]: x["CycleNo"] for x in db.query("SELECT [CycleID], [CycleNo] FROM [PaymentCycles]")}
    except Exception as e:
        if not mg.is_missing_table(e):
            raise
        cycle_no = {}
    paid_by_cert, pays_of = {}, {}
    for p in pays:                                       # grouped ONCE: re-scanning every payment for every certificate made this O(certificates x payments)
        paid_by_cert[p["CertificateID"]] = paid_by_cert.get(p["CertificateID"], 0.0) + float(p.get("Amount") or 0)
        pays_of.setdefault(p["CertificateID"], []).append(p)
    try:
        projects = {p["ProjectID"]: p["ProjectName"] for p in db.query("SELECT [ProjectID], [ProjectName] FROM [Projects]")}
    except Exception as e:
        if not mg.is_missing_table(e):
            raise
        projects = {}
    certs = []
    for g in db.query("SELECT * FROM [Certificates]"):
        c = contracts.get(g.get("ContractID")) or {}         # the contract is an optional link now
        sub_id = g.get("SubcontractorID") or c.get("SubcontractorID")
        if sub_id not in subs:
            continue                                         # no contractor can be found for it at all
        g = dict(g)
        g["StatusRaw"] = g.get("Status")
        g["StatusUnknown"] = classify_status(g.get("Status")) is None     # shown to the person; no payment until fixed
        g["Status"] = norm_status(g.get("Status"))
        g["Retention"], g["Net"], g["VAT"], g["Total"] = row_amounts(g)
        g["Value"] = certificate_value(g)                     # the certificate's value including VAT, before deductions
        g["Paid"] = round(paid_by_cert.get(g["CertificateID"], 0.0), 2)
        if g["Status"] == ST_PAID and not g["Paid"]:     # paid in an older version (no payment record yet)
            g["Paid"] = g["Total"]
        g["Remaining"] = round(0 if g["Status"] == ST_REJECTED else g["Total"] - g["Paid"], 2)
        mine_p = pays_of.get(g["CertificateID"], [])
        dates = [p.get("PaidAt") for p in mine_p if p.get("PaidAt")]
        g["LastPayDate"] = max(dates) if dates else g.get("PaymentDate")
        last_cycle = (max(mine_p, key=lambda p: p["PaymentID"]).get("CycleID") if mine_p else None) or g.get("CycleID")
        g["CycleNo"] = cycle_no.get(last_cycle)
        g["Project"] = projects.get(g.get("ProjectID")) or c.get("Project") or ""
        g["ContractNo"] = c.get("ContractNo")
        g["SubcontractorID"] = sub_id
        g["SubcontractorName"] = subs[sub_id].get("Name", "")
        certs.append(g)
    certs.sort(key=lambda g: (g.get("CertificateDate") or g.get("CreatedAt") or datetime.min), reverse=True)
    return subs, contracts, certs, pays


def counts(c):
    """A contract that counts in the contractor figures and the dashboards: a CONTRACTOR's contract that is not a draft, not an unsigned issue and not terminated.
    (Supplier agreements are not contractor business; a draft or an issued-but-unsigned contract is not a commitment yet.)"""
    return (c.get("PartyKind") or "مقاول") == "مقاول" and (c.get("Status") or "ساري") not in ("مسودة", "صادر", "مفسوخ")


def contract_figures(contract, certs):
    mine = [g for g in certs if g["ContractID"] == contract["ContractID"]]
    live = [g for g in mine if g["Status"] != ST_REJECTED]
    executed = round(sum(g.get("GrossAmount") or 0 for g in live), 2)
    value = float(contract.get("ContractValue") or 0)
    due = round(sum(g["Total"] for g in live), 2)
    paid = round(sum(g["Paid"] for g in live), 2)
    return {"certs": mine, "count": len(mine), "executed": executed, "value": value,
            "pct": round(executed / value * 100, 1) if value else None, "due": due, "paid": paid, "remaining": round(due - paid, 2)}


def ledger_totals(sub_id):
    """(debit, credit) of the EXECUTED movements typed by hand on the contractor's page - the same ones the account statement shows."""
    try:
        rows = db.query("SELECT * FROM [LedgerEntries] WHERE [SubcontractorID] = ?", (sub_id,))
    except Exception as e:
        if not mg.is_missing_table(e):
            raise
        return 0.0, 0.0
    done = [e for e in rows if e.get("Status") == "منفّذة"]
    return (round(sum(float(e.get("Amount") or 0) for e in done if e.get("Kind") == "مدين"), 2),
            round(sum(float(e.get("Amount") or 0) for e in done if e.get("Kind") == "دائن"), 2))


def sub_figures(sub_id, contracts, certs):
    """What one contractor is owed, from the SAME movements as his account statement:
        due  = the net due of his certificates + the debit entries (رد خصم، رد ضريبة ...)
        paid = the transfers paid on his certificates + the credit entries (دفعة مباشرة، مواد عليه ...)
        remaining = due - paid  (negative: he was paid more than due)"""
    mine = [c for c in contracts.values() if c.get("SubcontractorID") == sub_id and counts(c)]
    mine_certs = [g for g in certs if g["SubcontractorID"] == sub_id]
    live = [g for g in mine_certs if g["Status"] != ST_REJECTED]
    debit_entries, credit_entries = ledger_totals(sub_id)
    due, paid = round(sum(g["Total"] for g in live) + debit_entries, 2), round(sum(g["Paid"] for g in live) + credit_entries, 2)
    return {"contracts": len(mine), "value": round(sum(float(c.get("ContractValue") or 0) for c in mine), 2),
            "executed": round(sum(g.get("GrossAmount") or 0 for g in live), 2), "due": due, "paid": paid, "remaining": round(due - paid, 2),
            "certs": len(mine_certs)}

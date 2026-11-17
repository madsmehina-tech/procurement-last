# -*- coding: utf-8 -*-
"""The payment orders of a payment cycle ARE general vouchers (أوامر الصرف) - 29.0.

When the payment order of a contractor in a cycle is printed for the first time, a voucher is made for it:
  * its number is the next one of the vouchers (PR-0014 ...) at that moment - the order of printing decides the numbers;
  * its form is the voucher's form (the same paper, signatures, print settings, QR code) and it is listed with the other vouchers;
  * one line per certificate / direct payment the management approved, the beneficiary, its projects, the cycle as reference.
It is linked to its cycle (SourceRef «CYCLE:<cycle>:<contractor>»): printing again gives the SAME voucher (same number); if the approved
amounts changed in the meantime its lines are brought up to date. A cancelled one is replaced by a new one at the next print.
It never touches any account: the cycle's own transfer step is what records the payment in the contractor's statement
(PostChoice «لا»), so nothing is counted twice.
"""
import json
from datetime import datetime

import audit
import cycles_service as cy
import db
import voucher_service as vs

REQUEST_TYPE = "صرف مستخلصات (دورة صرف)"


def source_ref(cycle_id, sub_id):
    return "CYCLE:%d:%d" % (int(cycle_id), int(sub_id))


def _project_id(name):
    try:
        r = db.query("SELECT [ProjectID] FROM [Projects] WHERE [ProjectName] = ?", (name,))
        return r[0]["ProjectID"] if r else None
    except Exception:                                   # noqa - a voucher without a project id still prints
        return None


def _items(gp):
    out = []
    for l in gp["lines"]:
        if not l["Payable"]:
            continue
        if l["IsEntry"]:
            desc = "دفعة: %s" % (l["Description"] or "")
        else:
            desc = "مستخلص رقم %s — %s%s" % (l["CertNo"], l["Project"], (" — " + l["Site"]) if l["Site"] else "")
        out.append({"desc": desc[:250], "qty": 1, "price": round(float(l["Approved"]), 2), "total": round(float(l["Approved"]), 2)})
    return out


def _refs(cycle, gp):
    refs = [{"type": "أخرى", "no": "دورة الصرف رقم %s" % cycle["CycleNo"], "id": str(cycle["CycleID"])}]
    for l in gp["lines"]:
        if l["Payable"] and not l["IsEntry"]:
            refs.append({"type": "مستخلص", "no": str(l["CertNo"]), "id": str(l["CertificateID"])})
    return refs


def _figures(data, gp, company):
    cycle = data["cycle"]
    items = _items(gp)
    net = round(sum(i["total"] for i in items), 2)
    projects = list(dict.fromkeys(l["Project"] for l in gp["lines"] if l["Payable"] and l["Project"]))
    approved = cycle["Status"] in (cy.ST_APPROVED, cy.ST_DONE)
    import signatures
    signs = signatures.get(signatures.VOUCHER, company)
    notes = "الآيبان: \u2066%s\u2069" % cy.mask_iban(gp["iban"]) if gp.get("iban") else None      # kept left-to-right inside the Arabic line
    return {
        "ProjectID": _project_id(projects[0]) if projects else None, "ProjectName": projects[0] if projects else "",
        "CostCenter": "، ".join(projects)[:255], "PartyKind": "مقاول", "SupplierID": None, "SubcontractorID": gp["sub_id"], "PartyName": gp["name"],
        "RequestType": REQUEST_TYPE, "Department": (company or {}).get("VoucherDept") or None, "PayMethod": "تحويل بنكي", "PayStatement": None,
        "Description": (gp["statement"] or "صرف مستخلصات دورة الصرف رقم %s" % cycle["CycleNo"])[:255],
        "AmountMode": "incl", "VatPct": 0.0, "Subtotal": net, "VatAmount": 0.0, "Total": net, "Discount": 0.0, "Net": net, "NetSAR": net,
        "ItemsJson": json.dumps(items, ensure_ascii=False), "RefsJson": json.dumps(_refs(cycle, gp), ensure_ascii=False),
        "SignJson": json.dumps(signs, ensure_ascii=False), "Notes": notes, "ShowTotals": 0, "PostChoice": vs.NO,
        "CurrencyCode": "SAR", "ExchangeRate": 1.0,
        "Status": vs.ST_APPROVED if approved else vs.ST_DRAFT,
        "ApprovedBy": (cycle.get("ApprovedBy") or None) if approved else None, "ApprovedAt": (cycle.get("ApprovedAt") or None) if approved else None,
    }


def ensure(data, sub_id, user="", company=None):
    """The voucher of this contractor in this cycle (made now if it does not exist yet). Returns its id, or None when nothing is payable."""
    gp = next((g for g in data["groups"] if g["sub_id"] == int(sub_id)), None)
    if not gp or gp["payable"] <= cy.EPS:
        return None
    import contract_gen as cg
    company = company if company is not None else cg.company()
    ref = source_ref(data["cycle"]["CycleID"], sub_id)
    cols = _figures(data, gp, company)
    now = datetime.now()
    with db.transaction() as tx:
        have = [v for v in tx.query("SELECT [VoucherID], [VoucherNo], [Status], [Net], [ItemsJson] FROM [PaymentVouchers] WHERE [SourceRef] = ?", (ref,))
                if v["Status"] != vs.ST_CANCELLED]
        if have:
            v = have[0]
            if abs(float(v["Net"] or 0) - cols["Net"]) > 0.005 or (v["ItemsJson"] or "") != cols["ItemsJson"] or v["Status"] != cols["Status"]:
                cols["ModifiedAt"] = now
                tx.execute("UPDATE [PaymentVouchers] SET %s WHERE [VoucherID] = ?" % ", ".join("[%s] = ?" % c for c in cols), tuple(cols.values()) + (v["VoucherID"],))
                audit.log("voucher_edit", "VOUCHER", v["VoucherNo"], "حُدِّث من دورة الصرف رقم %s: %s" % (data["cycle"]["CycleNo"], "{:,.2f}".format(cols["Net"])))
            return v["VoucherID"]
        number = vs.next_no(tx)
        cols.update({"VoucherNo": number, "VoucherDate": now, "SourceRef": ref, "CreatedBy": user or "النظام", "CreatedAt": now, "ModifiedAt": now})
        vid = tx.execute_insert("INSERT INTO [PaymentVouchers] (%s) VALUES (%s)" % (", ".join("[%s]" % c for c in cols), ", ".join("?" for _ in cols)), tuple(cols.values()))
    audit.log("voucher_create", "VOUCHER", number, "من دورة الصرف رقم %s — %s — %s" % (data["cycle"]["CycleNo"], gp["name"], "{:,.2f}".format(cols["Net"])))
    return vid


def ensure_all(data, user="", company=None):
    """The vouchers of every contractor with something to pay, in the cycle's order (numbers follow that order)."""
    ids = []
    for gp in data["groups"]:
        vid = ensure(data, gp["sub_id"], user, company)
        if vid:
            ids.append(vid)
    return ids


def cycle_of(voucher):
    """(cycle id, cycle number) of a voucher made from a cycle, else None."""
    ref = (voucher or {}).get("SourceRef") or ""
    if not ref.startswith("CYCLE:"):
        return None
    try:
        cid = int(ref.split(":")[1])
    except (IndexError, ValueError):
        return None
    r = db.query("SELECT [CycleNo] FROM [PaymentCycles] WHERE [CycleID] = ?", (cid,))
    return (cid, r[0]["CycleNo"]) if r else None

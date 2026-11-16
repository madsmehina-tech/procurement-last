# -*- coding: utf-8 -*-
"""«تقييم الموردين» (29.0) - a score out of 100 per supplier, computed from what the system already records. Nobody types a score.

Four criteria (their weights are set by the management, default 40 / 30 / 20 / 10):
  time     الالتزام بموعد التوريد  - orders with an agreed delivery date: received complete on or before it = on time; past it and not
                                     complete (or completed after it) = late.   score = on time / measured x 100
  price    السعر                   - items bought from two or more suppliers: the supplier's average price of the item against the
                                     average of all suppliers (in riyals).   1 % dearer = -2 points, cheaper or equal = 100
  quality  جودة التوريد            - the receipt quality chosen at receipt: سليم 100 · بملاحظات 60 · مرفوض جزئياً 20 (average)
  docs     الأوراق                 - commercial register and tax certificate on file and not expired: 50 each
A criterion with no data for a supplier is left out and the others share its weight (old orders without the new data do not count against
anyone). Grade: A from 85 · B from 70 · C from 55 · D below.
"""
import json
import logging
from collections import defaultdict
from datetime import datetime

import db
from textnorm import norm_key

log = logging.getLogger(__name__)

CRITERIA = (("time", "الالتزام بموعد التوريد"), ("price", "السعر مقارنة بباقي الموردين"), ("quality", "جودة التوريد"), ("docs", "اكتمال الأوراق وسريانها"))
DEFAULT_WEIGHTS = {"time": 40, "price": 30, "quality": 20, "docs": 10}
QUALITY_SCORE = {"سليم": 100, "بملاحظات": 60, "مرفوض جزئياً": 20}
DOC_TYPES = ("السجل التجاري", "الشهادة الضريبية")


def grade(score):
    if score is None:
        return "—"
    return "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D"


# ------------------------------------------------------------------ weights
def weights():
    w = dict(DEFAULT_WEIGHTS)
    try:
        rows = db.query("SELECT [RatingJson] FROM [CompanyProfile] WHERE [ProfileID] = 1")
        if rows and rows[0].get("RatingJson"):
            saved = json.loads(rows[0]["RatingJson"]) or {}
            for k in w:
                if isinstance(saved.get(k), (int, float)) and 0 <= saved[k] <= 100:
                    w[k] = int(saved[k])
    except Exception:                                           # noqa - before m035: the defaults
        log.debug("rating weights not readable", exc_info=True)
    return w


def save_weights(raw):
    """raw: {key: text}. Returns (weights, error)."""
    w = {}
    for k, _label in CRITERIA:
        try:
            w[k] = int(float(str(raw.get(k, "")).strip() or 0))
        except ValueError:
            return None, "اكتب الأوزان أرقاماً."
        if not 0 <= w[k] <= 100:
            return None, "كل وزن بين 0 و 100."
    if sum(w.values()) != 100:
        return None, "مجموع الأوزان يجب أن يكون 100 (المكتوب %d)." % sum(w.values())
    text = json.dumps(w)
    if db.query("SELECT [ProfileID] FROM [CompanyProfile] WHERE [ProfileID] = 1"):
        db.execute("UPDATE [CompanyProfile] SET [RatingJson] = ? WHERE [ProfileID] = 1", (text,))
    else:
        db.execute("INSERT INTO [CompanyProfile] ([ProfileID], [RatingJson]) VALUES (1, ?)", (text,))
    return w, None


# ------------------------------------------------------------------ one order's delivery
def delivery_state(order, today=None):
    """(text, badge class) for the order page, or None when the order has no agreed date."""
    agreed = order.get("AgreedDeliveryDate")
    if not agreed:
        return None
    today = (today or datetime.now()).date()
    done, got = order.get("ReceiptStatus") == "مكتمل", order.get("ReceiptDate")
    if done and got:
        late = (got.date() - agreed.date()).days
        return ("وصل متأخراً %d يوماً" % late, "warning") if late > 0 else ("وصل في الموعد", "success")
    late = (today - agreed.date()).days
    if late > 0:
        return ("متأخر %d يوماً" % late, "danger")
    return ("باقٍ %d يوماً" % -late, "neutral") if late < 0 else ("موعده اليوم", "warning")


def _on_time(order, today):
    """True / False / None (not measurable yet)."""
    agreed = order.get("AgreedDeliveryDate")
    if not agreed:
        return None
    if order.get("ReceiptStatus") == "مكتمل" and order.get("ReceiptDate"):
        return order["ReceiptDate"].date() <= agreed.date()
    if today > agreed.date():
        return False
    return None


# ------------------------------------------------------------------ all suppliers
def _rate(order):
    try:
        r = float(order.get("ExchangeRate") or 1)
        return r if r > 0 else 1.0
    except (TypeError, ValueError):
        return 1.0


def compute(today=None):
    """[{id, name, orders, score, grade, parts: {key: {score, text}}, measured}] best first. Suppliers with no order are left out."""
    today = (today or datetime.now()).date()
    w = weights()
    sups = {r["SupplierID"]: r for r in db.query("SELECT * FROM [Suppliers]")}
    by_name = {norm_key(r.get("Name") or ""): sid for sid, r in sups.items()}
    orders = db.query("SELECT [PONumber], [SupplierID], [SupplierName], [OrderDate], [AgreedDeliveryDate], [ReceiptStatus], [ReceiptDate], "
                      "[ReceiptQuality], [ExchangeRate] FROM [PurchaseOrders]")
    of = defaultdict(list)
    po_sup, po_rate = {}, {}
    for o in orders:
        sid = o.get("SupplierID") or by_name.get(norm_key(o.get("SupplierName") or ""))
        if sid in sups:
            of[sid].append(o)
            po_sup[o["PONumber"]] = sid
            po_rate[o["PONumber"]] = _rate(o)

    # price: average unit price (riyals) of each item per supplier, for items bought from 2+ suppliers
    prices = defaultdict(lambda: defaultdict(list))
    for it in db.query("SELECT [PONumber], [ItemName], [UnitPrice] FROM [OrderItems]"):
        sid = po_sup.get(it["PONumber"])
        if sid and it.get("UnitPrice"):
            prices[norm_key(it["ItemName"] or "")][sid].append(float(it["UnitPrice"]) * po_rate[it["PONumber"]])
    ratios = defaultdict(list)
    for _item, per in prices.items():
        if len(per) < 2:
            continue
        avg = {sid: sum(v) / len(v) for sid, v in per.items()}
        market = sum(avg.values()) / len(avg)
        if market > 0:
            for sid, a in avg.items():
                ratios[sid].append(a / market)

    # papers on file (archive Documents, or the old columns)
    papers = defaultdict(dict)
    try:
        for d in db.query("SELECT [EntityKey], [DocType], [IsCurrent], [ExpiryDate] FROM [Documents] WHERE [EntityType] = ?", ("SUPPLIER",)):
            if d.get("IsCurrent") and d["DocType"] in DOC_TYPES:
                exp = d.get("ExpiryDate")
                papers[str(d["EntityKey"])][d["DocType"]] = not (exp and exp.date() < today)
    except Exception:                                           # noqa
        log.debug("supplier papers", exc_info=True)

    out = []
    for sid, olist in of.items():
        s = sups[sid]
        parts = {}
        flags = [x for x in (_on_time(o, today) for o in olist) if x is not None]
        if flags:
            ok = sum(1 for x in flags if x)
            parts["time"] = {"score": 100.0 * ok / len(flags), "text": "%d من %d أمراً في الموعد" % (ok, len(flags))}
        if ratios.get(sid):
            r = sum(ratios[sid]) / len(ratios[sid])
            pct = (r - 1) * 100
            parts["price"] = {"score": max(0.0, min(100.0, 100 - max(0.0, pct) * 2)),
                              "text": ("أعلى من متوسط الموردين بـ %.0f%%" % pct) if pct > 0.5 else ("أقل من متوسط الموردين بـ %.0f%%" % -pct) if pct < -0.5 else "مساوٍ لمتوسط الموردين"}
        q = [QUALITY_SCORE[o["ReceiptQuality"]] for o in olist if o.get("ReceiptQuality") in QUALITY_SCORE]
        if q:
            bad = sum(1 for o in olist if o.get("ReceiptQuality") in ("بملاحظات", "مرفوض جزئياً"))
            parts["quality"] = {"score": sum(q) / len(q), "text": "%d استلام، منها %d بملاحظات أو رفض جزئي" % (len(q), bad)}
        mine = papers.get(str(sid), {})
        legacy = {"السجل التجاري": bool(s.get("CRFile")), "الشهادة الضريبية": bool(s.get("TaxCertFile"))}
        have = [t for t in DOC_TYPES if mine.get(t, legacy[t])]
        missing = [t for t in DOC_TYPES if t not in have]
        parts["docs"] = {"score": 50.0 * len(have), "text": "كل الأوراق سارية" if not missing else "ناقص أو منتهٍ: " + "، ".join(missing)}
        for v in parts.values():
            v["score"] = round(v["score"], 1)
        used = {k: w[k] for k in parts if w.get(k)}
        total_w = sum(used.values())
        score = sum(parts[k]["score"] * used[k] for k in used) / total_w if total_w else None
        out.append({"id": sid, "name": s.get("Name") or "", "orders": len(olist), "score": round(score, 1) if score is not None else None,
                    "grade": grade(score), "parts": parts, "measured": [k for k in parts if k != "docs"], "few": len(flags) + len(q) < 2})
    out.sort(key=lambda x: (x["score"] is None, -(x["score"] or 0), x["name"]))
    return out


def for_supplier(supplier_id):
    return next((r for r in compute() if r["id"] == supplier_id), None)

# -*- coding: utf-8 -*-
"""
items_service.py - the items catalogue, built on the fly from the saved purchase orders (nothing to maintain).

Lines whose names are the same word written slightly differently (إسمنت / اسمنت، extra spaces, ة/ه ...) are one item;
the most used spelling is shown. For each item: times bought, quantities, value, prices (average / last / highest /
lowest), the latest supplier and the supplier that supplied it most often.
"""
from collections import Counter
from datetime import datetime

import db
from textnorm import clean_spaces, norm_key


NO_PROJECT, NO_SITE = "بدون مشروع", "بدون موقع"


def _names():
    """{ProjectID: name}, {SiteID: name} from the unified lists (an order points at them), so the same project written two ways is ONE."""
    try:
        return ({p["ProjectID"]: p["ProjectName"] for p in db.query("SELECT [ProjectID], [ProjectName] FROM [Projects]")},
                {s["SiteID"]: s["SiteName"] for s in db.query("SELECT [SiteID], [SiteName] FROM [Sites]")})
    except Exception:                                    # noqa - before the migration the tables may not exist
        return {}, {}


def _orders():
    projects, sites = _names()
    out = {}
    for o in db.query("SELECT * FROM [PurchaseOrders]"):
        out[o["PONumber"]] = dict(o, proj=projects.get(o.get("ProjectID")) or clean_spaces(o.get("Project")) or NO_PROJECT,
                                  site=sites.get(o.get("SiteID")) or clean_spaces(o.get("Location")) or NO_SITE)
    return out


def filter_options():
    """([project names], {project: [site names]}) of every purchase order - what the project / site filters offer."""
    by_project = {}
    for o in _orders().values():
        by_project.setdefault(o["proj"], set()).add(o["site"])
    return sorted(by_project, key=norm_key), {k: sorted(v, key=norm_key) for k, v in by_project.items()}


def build(project="", site=""):
    """The items catalogue. project / site (optional) keep only the purchases made for that project / site - the totals follow."""
    orders = _orders()
    pk, sk = norm_key(project), norm_key(site)
    groups = {}
    for ln in db.query("SELECT [PONumber], [LineNo], [ItemName], [Unit], [Qty], [UnitPrice] FROM [OrderItems]"):
        order = orders.get(ln["PONumber"])
        key = norm_key(ln["ItemName"])
        if not order or not key:
            continue
        if (pk and norm_key(order["proj"]) != pk) or (sk and norm_key(order["site"]) != sk):
            continue
        g = groups.setdefault(key, {"names": Counter(), "units": Counter(), "history": []})
        g["names"][clean_spaces(ln["ItemName"])] += 1
        g["units"][(ln["Unit"] or "").strip()] += 1
        qty, price = float(ln["Qty"] or 0), float(ln["UnitPrice"] or 0)
        g["history"].append({"date": order["OrderDate"] or order["CreatedAt"], "po": ln["PONumber"], "line": ln["LineNo"],
                             "supplier": order["SupplierName"] or "-", "project": order["proj"], "site": order["site"],
                             "qty": qty, "price": price, "total": round(qty * price, 2)})
    items = []
    for key, g in groups.items():
        hist = sorted(g["history"], key=lambda h: (h["date"] or datetime.min, h["po"], h["line"] or 0), reverse=True)
        total_qty = sum(h["qty"] for h in hist)
        total_value = sum(h["total"] for h in hist)
        prices = [h["price"] for h in hist if h["price"]]
        per_supplier = {}
        for h in hist:
            s = per_supplier.setdefault(h["supplier"], {"supplier": h["supplier"], "count": 0, "qty": 0.0, "value": 0.0, "last_price": h["price"]})
            s["count"] += 1
            s["qty"] += h["qty"]
            s["value"] += h["total"]                # hist is newest first, so last_price stays the newest
        suppliers = sorted(per_supplier.values(), key=lambda s: (-s["count"], -s["value"]))
        for s in suppliers:
            s["avg_price"] = round(s["value"] / s["qty"], 2) if s["qty"] else 0
        # change versus the previous purchase of the same item
        for i, h in enumerate(hist):
            prev = hist[i + 1]["price"] if i + 1 < len(hist) else None
            h["change"] = round((h["price"] - prev) / prev * 100, 1) if prev else None
        items.append({
            "key": key, "name": g["names"].most_common(1)[0][0], "unit": g["units"].most_common(1)[0][0],
            "count": len(hist), "total_qty": total_qty, "total_value": round(total_value, 2),
            "avg_price": round(total_value / total_qty, 2) if total_qty else (round(sum(prices) / len(prices), 2) if prices else 0),
            "last_price": hist[0]["price"], "last_date": hist[0]["date"], "max_price": max(prices) if prices else 0,
            "min_price": min(prices) if prices else 0, "last_supplier": hist[0]["supplier"],
            "top_supplier": suppliers[0]["supplier"], "suppliers": suppliers, "history": hist,
        })
    return items


SORTS = {
    "name": lambda i: norm_key(i["name"]), "count": lambda i: i["count"], "qty": lambda i: i["total_qty"],
    "value": lambda i: i["total_value"], "avg": lambda i: i["avg_price"], "last": lambda i: i["last_price"],
    "max": lambda i: i["max_price"], "min": lambda i: i["min_price"],
}


def sparkline(history, width=320, height=72, pad=6):
    """SVG points of the price over time. Newest on the left, oldest on the right (the page reads right to left)."""
    pts = [h["price"] for h in reversed(history)]           # oldest first
    if len(pts) < 2:
        return ""
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1.0
    step = (width - 2 * pad) / (len(pts) - 1)
    return " ".join("%.1f,%.1f" % (width - pad - i * step, height - pad - (p - lo) / span * (height - 2 * pad)) for i, p in enumerate(pts))


DEFAULT_UNITS = ["عدد", "حبة", "متر", "م2", "م3", "متر طولي", "كجم", "طن", "لتر", "كيس", "كرتون", "علبة", "رول", "طقم", "لفة", "قطعة", "مجموعة", "ساعة", "يوم", "شهر"]


def catalog(limit=800):
    """For the order form: (items bought before - name, unit, last price, last supplier - and the units in use).
    Choosing a known item fills its unit and last price."""
    items = sorted(build(), key=lambda i: (-i["count"], norm_key(i["name"])))[:limit]
    cat = [{"n": i["name"], "u": i["unit"], "p": i["last_price"], "s": i["last_supplier"]} for i in items]
    used = Counter(i["unit"] for i in items if i["unit"])
    units = [u for u, _ in used.most_common()] + [u for u in DEFAULT_UNITS if u not in used]
    return cat, units


def analysis(items):
    """The flat table for analysis (Pivot / Power BI): one row per ITEM x PROJECT x SITE -
    item, unit, project, site, total quantity, total value, average price, number of purchase orders."""
    rows = []
    for i in items:
        cells = {}
        for h in i["history"]:
            c = cells.setdefault((h["project"], h["site"]), {"qty": 0.0, "value": 0.0, "pos": set()})
            c["qty"] += h["qty"]
            c["value"] += h["total"]
            c["pos"].add(h["po"])
        for (project, site), c in cells.items():
            rows.append({"item": i["name"], "unit": i["unit"], "project": project, "site": site, "qty": round(c["qty"], 3), "value": round(c["value"], 2),
                         "avg": round(c["value"] / c["qty"], 2) if c["qty"] else 0.0, "orders": len(c["pos"])})
    rows.sort(key=lambda r: (norm_key(r["item"]), norm_key(r["project"]), norm_key(r["site"])))
    return rows

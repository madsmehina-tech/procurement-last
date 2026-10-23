# -*- coding: utf-8 -*-
"""cycle_files.py - the cycle's printed statement (كشف الدورة) and payment orders: HTML always, PDF in the archive when a browser exists."""
import base64
import functools
import os
import tempfile

from flask import render_template

import archive
import arabic_numbers
import config
import cycles_service as cy
import pdfgen

T_DRAFT, T_FINAL, T_ORDER = "كشف الدورة (للإدارة)", "الكشف النهائي", "أمر الصرف"
# the statement's columns (key, heading, grid width): the sheet of the real workflow
# (key, heading, relative width). The printed table uses a FIXED layout: the widths of the columns that are shown are turned into
# percentages of the page, so the table can never be wider than the paper (A4 landscape) whatever columns are chosen.
COLS = [("seq", "مسلسل", 4.4), ("sub", "اسم المقاول", 11), ("ref", "رقم المستخلص", 6.4), ("project", "المشروع", 9.5), ("site", "الموقع", 8.5),
        ("value", "قيمة المستخلص", 7), ("paid_before", "المصروف سابقاً", 7), ("remaining", "المتبقي", 7),
        ("total", "الإجمالي", 7), ("sub_approved", "مبلغ الصرف المعتمد", 8), ("status", "الحالة", 8.5),
        ("decision", "القرار", 7), ("approved", "مبلغ الصرف", 7), ("notes", "الملاحظات", 10.5)]
GROUP_COLS = ("total", "sub_approved")                  # one cell for ALL the certificates of a contractor (merged vertically)
DEFAULT_COLS = tuple(k for k, _h, _w in COLS)
SHORT_DECISION = {cy.D_FULL: "يصرف بالكامل", cy.D_PART: "يصرف جزئياً", cy.D_DEFER: "مؤجل"}


@functools.lru_cache(maxsize=1)
def logo():
    """The company logo as a data URI - read once; every printed page (browser print AND PDF) uses it, so it always appears."""
    try:
        with open(os.path.join(config.BASE_DIR, "static", "company_logo.png"), "rb") as f:
            return "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")
    except OSError:
        return ""


def parse_options(args, default_mode=None):
    """mode: «blank» = the decision / amount / notes cells are empty (to be written by hand in the meeting); «values» = as entered."""
    mode = args.get("mode") if args.get("mode") in ("blank", "values") else (default_mode or "values")
    raw = args.get("cols")
    cols = [c for c in (raw.split(",") if raw else []) if c in {k for k, _h, _w in COLS}] or list(DEFAULT_COLS)
    return mode, cols


def default_mode_for(cycle):
    return "blank" if cycle["Status"] == cy.ST_DRAFT else "values"


def _n(x):
    return "{:,.0f}".format(x)


def sheet_rows(data, mode, cols):
    """Rows of the statement. «الإجمالي» and «مبلغ الصرف المعتمد» are ONE merged cell per contractor (its open certificates together)."""
    blank = mode == "blank"
    out, seq = [], 0
    for gp in data["groups"]:
        for i, l in enumerate(gp["lines"]):
            seq += 1
            decided = bool(l["Decision"]) and not blank
            val = {"seq": str(seq), "sub": l["SubName"], "ref": str(l["CertNo"]), "project": l["Project"], "site": l["Site"], "value": _n(l["Total"]),
                   "paid_before": _n(l["PaidBefore"]), "remaining": _n(l["Requested"]), "status": l["StatusLabel"],
                   "decision": SHORT_DECISION.get(l["Decision"], "") if decided else "", "approved": (_n(l["Approved"]) if decided else ""),
                   "notes": ("دفعة: " + l["Description"] + ((" — " + l["Note"]) if l["Note"] and not blank else "")) if l["IsEntry"] else ("" if blank else l["Note"])}
            if l["IsEntry"]:
                val["ref"] = "—"
            cells = []
            for k, _h, _w in COLS:
                if k not in cols:
                    continue
                if k in GROUP_COLS:
                    if i == 0:                                           # the merged cell is written once, on the contractor's first row
                        v = _n(gp["total"]) if k == "total" else ("" if blank or not gp["decided"] else _n(gp["approved"]))
                        cells.append({"k": k, "v": v, "span": len(gp["lines"]), "cls": "grp"})
                    continue
                cells.append({"k": k, "v": val[k], "span": 1, "cls": "b" if k in ("value", "remaining", "approved") else ("num" if k in ("seq", "ref", "paid_before") else "")})
            out.append({"cells": cells, "stop": decided and l["Decision"] == cy.D_DEFER, "first": i == 0})
    return out


def sheet_context(data, mode, cols):
    t = data["totals"]
    picked = [(k, h, w) for k, h, w in COLS if k in cols]
    total_w = sum(w for _k, _h, w in picked) or 1
    shown = [(k, h, round(w / total_w * 100, 2)) for k, h, w in picked]
    blank = mode == "blank"
    sub = {cy.ST_DRAFT: "مرحلة الإنشاء", cy.ST_WAITING: "مرحلة قرار الإدارة", cy.ST_APPROVED: "المرحلة النهائية · تنفيذ التحويل", cy.ST_DONE: "الدورة مغلقة"}.get(data["cycle"]["Status"], "")
    return {"d": data, "mode": mode, "cols": shown, "rows": sheet_rows(data, mode, cols), "blank": blank,
            "total_all": _n(t["all"]), "total_approved": _n(t["approved"]), "sub_title": sub}


def sheet_html(data, mode=None, cols=None, pdf=False):
    mode, cols = parse_options({"mode": mode, "cols": ",".join(cols) if cols else None}, default_mode_for(data["cycle"]))
    return render_template("cycle_print.html", logo_src=logo() if pdf else "", pdf_mode=pdf, **sheet_context(data, mode, cols))


def order_context(data, sub_id):
    """ONE payment order per contractor: the amount of its transfer, and the wording of the real case (البيان)."""
    gp = next((g for g in data["groups"] if g["sub_id"] == int(sub_id)), None)
    if not gp or gp["payable"] <= cy.EPS:
        return None
    return {"group": gp, "cycle": data["cycle"], "order_no": gp["order_no"], "iban_masked": cy.mask_iban(gp["iban"]), "amount": gp["payable"],
            "statement": gp["statement"], "words": arabic_numbers.amount_to_words(gp["payable"])}


def order_html(data, sub_id, pdf=False):
    ctx = order_context(data, sub_id)
    return render_template("payment_order.html", logo_src=logo() if pdf else "", pdf_mode=pdf, orders=[ctx], **ctx) if ctx else None


def all_orders_html(data, pdf=False):
    ctxs = [c for c in (order_context(data, gp["sub_id"]) for gp in data["groups"]) if c]
    return render_template("payment_order.html", logo_src=logo() if pdf else "", pdf_mode=pdf, orders=ctxs, **(ctxs[0] if ctxs else {})) if ctxs else None


def _pdf_bytes(html):
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        pdfgen.html_to_pdf(html, path)
        with open(path, "rb") as f:
            return f.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def save_sheet(cycle_id, mode=None, cols=None, user=""):
    """PDF of the statement into the cycle's folder. Returns (record or None, error text)."""
    data = cy.load(cycle_id)
    if not data:
        return None, "دورة الصرف غير موجودة"
    mode, cols = parse_options({"mode": mode, "cols": ",".join(cols) if cols else None}, default_mode_for(data["cycle"]))
    doc_type = T_DRAFT if mode == "blank" else T_FINAL
    try:
        folder = archive.ensure_cycle_folder(cycle_id)
        pdf = _pdf_bytes(sheet_html(data, mode, cols, pdf=True))
        name = "%s - دورة %s.pdf" % (doc_type, data["cycle"]["CycleNo"])
        return archive.store_generated("CYCLE", cycle_id, archive.cycle_doc_folder_rel(folder, doc_type), doc_type, pdf, name, user), ""
    except pdfgen.PdfError as e:
        return None, str(e)
    except (OSError, ValueError) as e:
        return None, "تعذّر الكتابة في الأرشيف: %s" % e


def save_order(cycle_id, sub_id, user=""):
    data = cy.load(cycle_id)
    ctx = order_context(data, sub_id) if data else None
    if not ctx:
        return None, "لا مبلغ معتمد للصرف لهذا المقاول"
    try:
        folder = archive.ensure_cycle_folder(cycle_id)
        pdf = _pdf_bytes(order_html(data, sub_id, pdf=True))
        name = "%s - %s.pdf" % (ctx["order_no"], ctx["group"]["name"])
        return archive.store_generated("CYCLE", cycle_id, archive.cycle_doc_folder_rel(folder, T_ORDER), T_ORDER, pdf, name, user), ""
    except pdfgen.PdfError as e:
        return None, str(e)
    except (OSError, ValueError) as e:
        return None, "تعذّر الكتابة في الأرشيف: %s" % e

# -*- coding: utf-8 -*-
"""
contract_gen.py - generated contracts: saving the data, building what the template needs, making the DOCX and PDF, the signed copy, the history.

A contract made by the system is ONE record of the existing Contracts table (Origin «مولَّد») - so it appears in the same register as the
contracts that were signed outside and archived (Origin «مؤرشف»), is linked to the same contractor / supplier, project and certificates, and
its signed copy is uploaded to the SAME record. The parties' data is read from the contractor / supplier / company records, never retyped.
"""
import os
import tempfile
import threading
from datetime import datetime

import archive
import arabic_numbers
import contract_pdf
import contract_render
import contract_templates as templates
import contract_vars as V
import db
import money_guards as mg
import write_lock
import masterdata
from textnorm import clean_spaces, norm_key

VAT_RATE = 0.15
PRICE_MODELS = ("مقطوعية", "بالوحدة")
ST_DRAFT, ST_ISSUED, ST_ACTIVE = "مسودة", "صادر", "ساري"
WEEKDAYS = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
ORDINALS = ["الأولى", "الثانية", "الثالثة", "الرابعة", "الخامسة", "السادسة", "السابعة", "الثامنة"]


class ContractError(Exception):
    """A rule was broken - the message is shown as is."""


# ---------------------------------------------------------------- small helpers
def _num(x):
    """A number typed in the contract form -> float, or None when blank or not a sane number (NaN, infinity, a minus sign, letters ...)."""
    s = str(x if x is not None else "").strip()
    if s == "":
        return None
    try:
        return mg.parse_money(s, decimals=4)
    except ValueError:
        return None


def money(v):
    if v is None:
        return None
    return "{:,.0f}".format(v) if abs(v - round(v)) < 0.005 else "{:,.2f}".format(v)


def date_str(d):
    return d.strftime("%d/%m/%Y") if d else None


def parse_date(raw):
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime((raw or "").strip(), fmt)
        except ValueError:
            continue
    return None


def words_sar(amount):
    """«فقط ستة وأربعون ألف ريال سعودي لا غير» - the contract style: hundreds written joined (خمسمائة) and the currency with its adjective."""
    if amount is None:
        return None
    amount = round(float(amount), 2)
    if not (0 <= amount < 1_000_000_000):
        return None
    whole, halalas = int(amount), int(round((amount - int(amount)) * 100))
    phrase = arabic_numbers._noun_phrase(whole, 'ريال واحد', 'ريالان', 'ريالات', 'ريالاً') if whole else "صفر ريال"
    for plain, adjective in (("ريال واحد", "ريال سعودي واحد"), ("ريالان", "ريالان سعوديان"), ("ريالات", "ريالات سعودية"), ("ريالاً", "ريالاً سعودياً"), ("ريال", "ريال سعودي")):
        if phrase.endswith(plain):
            phrase = phrase[:-len(plain)] + adjective
            break
    for a_, b_ in (("ثلاث مائة", "ثلاثمائة"), ("أربع مائة", "أربعمائة"), ("خمس مائة", "خمسمائة"), ("ست مائة", "ستمائة"), ("سبع مائة", "سبعمائة"), ("ثمان مائة", "ثمانمائة"), ("تسع مائة", "تسعمائة")):
        phrase = phrase.replace(a_, b_)
    tail = (" و" + arabic_numbers._noun_phrase(halalas, "هللة واحدة", "هللتان", "هللات", "هللة")) if halalas else ""
    return "فقط %s%s لا غير" % (phrase, tail)


# ---------------------------------------------------------------- parties and numbering
def company():
    rows = db.query("SELECT * FROM [CompanyProfile] WHERE [ProfileID] = 1")
    return rows[0] if rows else {}


def party(kind, pid):
    """The contractor's or supplier's legal data in one shape."""
    if kind == "مورد":
        rows = db.query("SELECT * FROM [Suppliers] WHERE [SupplierID] = ?", (pid,))
        if not rows:
            return None
        r = rows[0]
        return {"kind": "مورد", "id": pid, "name": r["Name"], "cr": r.get("CR"), "vat": r.get("TaxNumber"), "unified": r.get("UnifiedNo"), "address": r.get("Address"), "phone": r.get("Phone"),
                "email": r.get("RepEmail"), "rep": r.get("RepName"), "title": r.get("RepTitle"), "bank": r.get("BankName"), "iban": r.get("BankAccount"), "holder": r.get("AccountHolder")}
    rows = db.query("SELECT * FROM [Subcontractors] WHERE [SubcontractorID] = ?", (pid,))
    if not rows:
        return None
    r = rows[0]
    return {"kind": "مقاول", "id": pid, "name": r["Name"], "cr": r.get("CR"), "vat": r.get("TaxNumber"), "unified": r.get("UnifiedNo"), "address": r.get("Address"), "phone": r.get("Phone"),
            "email": r.get("Email"), "rep": r.get("RepName"), "title": r.get("RepTitle"), "bank": r.get("BankName"), "iban": r.get("BankAccount"), "holder": r.get("AccountHolder")}


def _rows(form, *names):
    """The values of several same-named form arrays, row by row (an array that is shorter is padded with empty text)."""
    from itertools import zip_longest
    return zip_longest(*[form.getlist(n) for n in names], fillvalue="")


def parse_party(raw):
    """«sub:12» / «sup:5» -> ('مقاول', 12) / ('مورد', 5)."""
    kind, _, pid = (raw or "").partition(":")
    if pid.isdigit() and kind in ("sub", "sup"):
        return ("مورد" if kind == "sup" else "مقاول"), int(pid)
    return None, None


def next_number(tx, prefix):
    year = datetime.now().year
    taken = {norm_key(c["ContractNo"] or "") for c in tx.query("SELECT [ContractNo] FROM [Contracts]")}
    row = tx.query("SELECT * FROM [NumberSeries] WHERE [SeriesKey] = ? AND [SeriesYear] = ?", (prefix, year))
    n = (row[0]["LastNo"] if row else 0) or 0
    while True:
        n += 1
        number = "%s-%d-%03d" % (prefix, year, n)
        if norm_key(number) not in taken:
            break
    if row:
        tx.execute("UPDATE [NumberSeries] SET [LastNo] = ? WHERE [SeriesKey] = ? AND [SeriesYear] = ?", (n, prefix, year))
    else:
        tx.execute("INSERT INTO [NumberSeries] ([SeriesKey], [SeriesYear], [LastNo]) VALUES (?, ?, ?)", (prefix, year, n))
    return number


def history(cid, action, detail="", user=""):
    db.execute("INSERT INTO [ContractHistory] ([ContractID], [At], [Action], [Detail], [ByUser]) VALUES (?, ?, ?, ?, ?)", (cid, datetime.now(), action, (detail or "")[:255], user))


# ---------------------------------------------------------------- reading a contract
def load(cid):
    rows = db.query("SELECT * FROM [Contracts] WHERE [ContractID] = ?", (cid,))
    if not rows:
        return None
    lines = sorted(db.query("SELECT * FROM [ContractLines] WHERE [ContractID] = ?", (cid,)), key=lambda l: (l["Kind"] or "", l["LineNo"] or 0))
    tr = sorted(db.query("SELECT * FROM [ContractTranches] WHERE [ContractID] = ?", (cid,)), key=lambda t: t["TrancheNo"] or 0)
    data = {d["FieldKey"]: d["FieldValue"] for d in db.query("SELECT * FROM [ContractData] WHERE [ContractID] = ?", (cid,))}
    return {"c": rows[0], "price_lines": [l for l in lines if l["Kind"] == "سعر"], "spec_lines": [l for l in lines if l["Kind"] == "مواصفات"], "tranches": tr, "data": data}


def totals(price_lines, model):
    """(value before VAT or None, VAT, value with VAT) from the price lines (stored WITHOUT VAT)."""
    parts = []
    for l in price_lines:
        qty = l.get("Qty") if l.get("Qty") not in (None, 0) else (1.0 if model == "مقطوعية" else None)
        if qty is not None and l.get("UnitPrice") is not None:
            parts.append(qty * l["UnitPrice"])
    if not parts or (model == "بالوحدة" and len(parts) != len(price_lines)):
        return None, None, None
    ex = round(sum(parts), 2)
    vat = round(ex * VAT_RATE, 2)
    return ex, vat, round(ex + vat, 2)


# ---------------------------------------------------------------- what the template needs
def build_values(cid):
    d = load(cid)
    c = d["c"]
    comp = company()
    p2 = party(c.get("PartyKind") or "مقاول", c.get("SupplierID") if c.get("PartyKind") == "مورد" else c.get("SubcontractorID")) or {}
    model = c.get("PriceModel") or "مقطوعية"
    ex, vat, incl = totals(d["price_lines"], model)
    when = c.get("ContractDate") or c.get("CreatedAt") or datetime.now()
    v = {
        "رقم_العقد": c.get("ContractNo"), "تاريخ_العقد": date_str(when), "يوم_العقد": WEEKDAYS[when.weekday()], "مدينة_العقد": comp.get("City"),
        "الطرف_الأول_الاسم": comp.get("LegalName"), "الطرف_الأول_السجل": comp.get("CR"), "الطرف_الأول_الرقم_الضريبي": comp.get("VatNo"), "الطرف_الأول_الرقم_الوطني": comp.get("UnifiedNo"),
        "الطرف_الأول_العنوان": comp.get("Address"), "الطرف_الأول_المدينة": comp.get("City"), "الطرف_الأول_الهاتف": comp.get("Phone"), "الطرف_الأول_البريد": comp.get("Email"),
        "الطرف_الأول_الممثل": comp.get("RepName"), "الطرف_الأول_الصفة": comp.get("RepTitle"), "الطرف_الأول_البنك": comp.get("BankName"), "الطرف_الأول_الآيبان": comp.get("IBAN"),
        "الطرف_الثاني_الاسم": p2.get("name"), "الطرف_الثاني_السجل": p2.get("cr"), "الطرف_الثاني_الرقم_الوطني": p2.get("unified"), "الطرف_الثاني_الرقم_الضريبي": p2.get("vat"),
        "الطرف_الثاني_العنوان": p2.get("address"), "الطرف_الثاني_الهاتف": p2.get("phone"), "الطرف_الثاني_البريد": p2.get("email"), "الطرف_الثاني_الممثل": p2.get("rep"),
        "الطرف_الثاني_الصفة": p2.get("title"), "الطرف_الثاني_البنك": p2.get("bank"), "الطرف_الثاني_الآيبان": p2.get("iban"), "الطرف_الثاني_اسم_الحساب": p2.get("holder") or p2.get("name"),
        "المشروع": c.get("Project"), "الموقع": c.get("SiteName"), "نطاق_العمل": c.get("Scope"), "رقم_عرض_السعر": c.get("QuoteNo"), "تاريخ_عرض_السعر": date_str(c.get("QuoteDate")),
        "القيمة_قبل_الضريبة": money(ex), "الضريبة": money(vat), "القيمة_شاملة_الضريبة": money(incl), "القيمة_بالحروف": words_sar(incl), "نسبة_الضريبة": "%d%%" % round(VAT_RATE * 100),
        "مدة_التنفيذ_بالأيام": c.get("DurationDays"), "تاريخ_البداية": date_str(c.get("StartDate")), "تاريخ_النهاية": date_str(c.get("EndDate")),
        "غرامة_التأخير_اليومية": money(c.get("PenaltyPerDay")), "الحد_الأقصى_للغرامة": money(c.get("PenaltyMaxPct")), "مدة_الضمان": c.get("WarrantyText"),
    }
    if model == "مقطوعية":
        v["عبارة_القيمة"] = ("%s شاملة ضريبة القيمة المضافة." % words_sar(incl)) if incl is not None else None
        v["طريقة_الاحتساب"] = "السعر مقطوع وثابت ولا يخضع لإعادة القياس، ويشمل كل ما يلزم لتنفيذ الأعمال تنفيذاً كاملاً وسليماً وفق المخططات والمواصفات المعتمدة وإن لم يرد ذكره صراحة في هذا العقد."
    else:
        v["عبارة_القيمة"] = (("القيمة التقديرية للعقد %s ريال قبل الضريبة (%s ريال شاملة الضريبة)، وتُحتسب القيمة النهائية بالكميات المنفذة فعلياً." % (money(ex), money(incl))) if ex is not None
                             else "تُحتسب القيمة النهائية بالكميات المنفذة فعلياً وفق أسعار الوحدة الواردة في الجدول أعلاه.")
        v["طريقة_الاحتساب"] = "تُحتسب الأعمال بسعر الوحدة الوارد بالجدول وبالكميات المنفذة فعلياً بعد القياس والاعتماد من الطرف الأول، والكميات الواردة بالجدول تقديرية وتخضع لإعادة القياس."
    for k, val in d["data"].items():
        if not k.startswith("_"):
            v[V.norm_key(k)] = val
    return {k: (str(x) if x is not None and not isinstance(x, str) else x) for k, x in v.items()}


def build_tables(cid):
    d = load(cid)
    c = d["c"]
    model = c.get("PriceModel") or "مقطوعية"
    ex_total, _vat, _incl = totals(d["price_lines"], model)
    if model == "مقطوعية":
        rows, sx, sv = [], 0.0, 0.0
        for i, l in enumerate(d["price_lines"], 1):
            qty = l["Qty"] if l.get("Qty") not in (None, 0) else 1.0
            ex = round(qty * (l["UnitPrice"] or 0), 2)
            vat = round(ex * VAT_RATE, 2)
            sx, sv = sx + ex, sv + vat
            rows.append([str(i), l["Description"] or None, l.get("Unit") or "مقطوعية", money(ex), money(vat), money(ex + vat)])
        if rows:
            rows.append(["", "الإجمالي", "", money(round(sx, 2)), money(round(sv, 2)), money(round(sx + sv, 2))])
        else:
            rows = [["1", None, None, None, None, None]]
        price = {"header": ["م", "البند", "الوحدة", "السعر قبل الضريبة (ريال)", "ضريبة القيمة المضافة 15% (ريال)", "السعر شامل الضريبة (ريال)"], "rows": rows, "widths": [0.06, 0.34, 0.1, 0.17, 0.17, 0.16],
                 "align": ["center", "right", "center", "center", "center", "center"], "bold_last": len(d["price_lines"]) > 0}
    else:
        has_qty = any(l.get("Qty") for l in d["price_lines"])
        incl = lambda l: money(round((l.get("UnitPrice") or 0) * (1 + VAT_RATE), 2)) if l.get("UnitPrice") is not None else None
        if has_qty:
            rows = [[str(i), l["Description"] or None, l.get("Unit") or None, money(l.get("Qty")), money(l.get("UnitPrice")), incl(l)] for i, l in enumerate(d["price_lines"], 1)] or [["1", None, None, None, None, None]]
            price = {"header": ["م", "البند", "الوحدة", "الكمية التقديرية", "سعر الوحدة قبل الضريبة (ريال)", "سعر الوحدة شامل الضريبة (ريال)"], "rows": rows, "widths": [0.06, 0.34, 0.1, 0.14, 0.18, 0.18],
                     "align": ["center", "right", "center", "center", "center", "center"]}
        else:                                                    # priced per unit with no estimate (per trip, per square metre ...): no quantity column
            rows = [[str(i), l["Description"] or None, l.get("Unit") or None, money(l.get("UnitPrice")), incl(l)] for i, l in enumerate(d["price_lines"], 1)] or [["1", None, None, None, None]]
            price = {"header": ["م", "البند", "الوحدة", "سعر الوحدة قبل الضريبة (ريال)", "سعر الوحدة شامل الضريبة (ريال)"], "rows": rows, "widths": [0.06, 0.4, 0.14, 0.2, 0.2],
                     "align": ["center", "right", "center", "center", "center"]}
    specs = [[str(i), l["Description"] or None, (l.get("Note") or None)] for i, l in enumerate(d["spec_lines"], 1)] or [["%d" % i, None, None] for i in (1, 2, 3)]
    base = ex_total if ex_total is not None else None
    trs = []
    for t in d["tranches"]:
        amount = t.get("Amount")
        if amount is None and t.get("Pct") is not None and base is not None:
            amount = round(base * t["Pct"] / 100, 2)
        trs.append([t.get("Label") or ORDINALS[min((t["TrancheNo"] or 1) - 1, len(ORDINALS) - 1)], ("%s%%" % money(t["Pct"])) if t.get("Pct") is not None else None, money(amount), t.get("DueText") or None])
    trs = trs or [[o, None, None, None] for o in ORDINALS[:3]]
    return {"جدول_الأسعار": price, "جدول_المواصفات": {"header": ["م", "العمل", "الوصف"], "rows": specs, "widths": [0.06, 0.22, 0.72], "align": ["center", "center", "right"]},
            "جدول_الدفعات": {"header": ["الدفعة", "النسبة", "المبلغ قبل الضريبة (ريال)", "موعد / شرط الاستحقاق"], "rows": trs, "widths": [0.14, 0.12, 0.2, 0.54], "align": ["center", "center", "center", "right"]}}


def missing_fields(cid):
    """What the chosen template asks for that has no value yet (it will stay as yellow dots)."""
    c = db.query("SELECT [TemplateVersionID] FROM [Contracts] WHERE [ContractID] = ?", (cid,))
    ver = db.query("SELECT * FROM [ContractTemplateVersions] WHERE [VersionID] = ?", ((c[0].get("TemplateVersionID") if c else 0) or 0,))
    if not ver:
        return []
    info = templates.fields_of(ver[0])
    vals = build_values(cid)
    return [f for f in info.get("fields", []) if not f.get("optional") and not (vals.get(f["key"]) or "").strip()]


# ---------------------------------------------------------------- saving
def save(form, user, contract_id=None):
    """The wizard's POST -> the contract record + its lines, tranches and extra fields. Returns the contract id. Missing data is allowed (it stays yellow)."""
    kind, pid = parse_party(form.get("party"))
    if not kind or not party(kind, pid):
        raise ContractError("اختر المقاول أو المورد.")
    tpl = templates.get(int(form.get("template_id") or 0)) if (form.get("template_id") or "").isdigit() else None
    if not tpl or not tpl.get("Active"):
        raise ContractError("اختر قالب العقد.")
    ver = templates.current_version(tpl["TemplateID"])
    if not ver:
        raise ContractError("القالب «%s» بلا ملف؛ ارفع ملف القالب أولًا من «قوالب العقود»." % tpl["Name"])
    if kind != tpl["PartyKind"]:
        raise ContractError("القالب «%s» لـ%s وليس لـ%s." % (tpl["Name"], "المقاولين" if tpl["PartyKind"] == "مقاول" else "الموردين", "المقاولين" if kind == "مقاول" else "الموردين"))
    model = form.get("price_model") if form.get("price_model") in PRICE_MODELS else "مقطوعية"
    incl = form.get("prices_incl_vat") == "1"
    project, site = clean_spaces(form.get("project")), clean_spaces(form.get("site"))
    price_lines = []
    for desc, unit, qty, price in _rows(form, "line_desc", "line_unit", "line_qty", "line_price"):
        if clean_spaces(desc) or _num(price) is not None:
            p = _num(price)
            if p is not None and incl:
                p = round(p / (1 + VAT_RATE), 4)
            price_lines.append((clean_spaces(desc), clean_spaces(unit) or ("مقطوعية" if model == "مقطوعية" else ""), _num(qty), p))
    specs = [(clean_spaces(n), (d or "").strip()) for n, d in _rows(form, "spec_name", "spec_desc") if clean_spaces(n)]
    tranches = [(clean_spaces(a), _num(b), _num(c), clean_spaces(d)) for a, b, c, d in _rows(form, "tr_label", "tr_pct", "tr_amount", "tr_due")
                if clean_spaces(a) or _num(b) is not None or _num(c) is not None or clean_spaces(d)]
    custom = {k[3:]: (v or "").strip() for k, v in form.items() if k.startswith("cf_") and (v or "").strip()}
    ex, _v, _i = totals([{"Qty": q, "UnitPrice": p} for _d, _u, q, p in price_lines], model)
    vat = round(ex * VAT_RATE, 2) if ex is not None else None
    cdate, start, end = parse_date(form.get("contract_date")) or datetime.now(), parse_date(form.get("start_date")), parse_date(form.get("end_date"))
    duration = int(form["duration_days"]) if (form.get("duration_days") or "").strip().isdigit() else None
    with db.transaction() as tx:
        pr_id = site_id = None
        if project:
            pr_id, project, _n = masterdata.resolve_project(project, tx)
            if site:
                site_id, site, _m = masterdata.resolve_site(pr_id, site, tx)
        cols = {"SubcontractorID": pid if kind == "مقاول" else None, "SupplierID": pid if kind == "مورد" else None, "PartyKind": kind, "Project": project or None, "SiteName": site or None,
                "ProjectID": pr_id, "ContractValue": ex, "Notes": (form.get("notes") or "").strip(), "ContractType": tpl["ContractType"], "Origin": "مولَّد", "Scope": (form.get("scope") or "").strip(),
                "PriceModel": model, "ValueExVat": ex, "VatAmount": vat, "DurationDays": duration, "PenaltyPerDay": _num(form.get("penalty_per_day")), "PenaltyMaxPct": _num(form.get("penalty_max_pct")),
                "WarrantyText": clean_spaces(form.get("warranty")) or None, "TemplateVersionID": ver["VersionID"], "ContractDate": cdate, "QuoteNo": clean_spaces(form.get("quote_no")) or None,
                "QuoteDate": parse_date(form.get("quote_date")), "StartDate": start, "EndDate": end, "SourcePO": clean_spaces(form.get("source_po")) or None,
                "SourceCompareID": int(form["source_compare"]) if (form.get("source_compare") or "").isdigit() else None}
        if contract_id is None:
            cols.update({"ContractNo": next_number(tx, tpl["NumberPrefix"] or "CT"), "Status": ST_DRAFT, "CreatedAt": datetime.now(), "Revision": 0})
            names = list(cols)
            contract_id = tx.execute_insert("INSERT INTO [Contracts] (%s) VALUES (%s)" % (", ".join("[%s]" % n for n in names), ", ".join("?" for _ in names)), tuple(cols[n] for n in names))
            created = True
        else:
            old = tx.query("SELECT * FROM [Contracts] WHERE [ContractID] = ?", (contract_id,))
            if not old or old[0].get("Origin") != "مولَّد":
                raise ContractError("هذا العقد مؤرشف (وُقّع خارج النظام) ولا يُعدَّل بهذه الشاشة.")
            names = list(cols)
            tx.execute("UPDATE [Contracts] SET %s WHERE [ContractID] = ?" % ", ".join("[%s] = ?" % n for n in names), tuple(cols[n] for n in names) + (contract_id,))
            created = False
        tx.execute("DELETE FROM [ContractSites] WHERE [ContractID] = ?", (contract_id,))
        if site:
            tx.execute("INSERT INTO [ContractSites] ([ContractID], [SiteName], [CreatedAt], [SiteRefID]) VALUES (?, ?, ?, ?)", (contract_id, site, datetime.now(), site_id))
        for table in ("ContractLines", "ContractTranches", "ContractData"):
            tx.execute("DELETE FROM [%s] WHERE [ContractID] = ?" % table, (contract_id,))
        for i, (desc, unit, qty, p) in enumerate(price_lines, 1):
            tx.execute("INSERT INTO [ContractLines] ([ContractID], [Kind], [LineNo], [Description], [Unit], [Qty], [UnitPrice]) VALUES (?, 'سعر', ?, ?, ?, ?, ?)", (contract_id, i, desc, unit, qty, p))
        for i, (n, d) in enumerate(specs, 1):
            tx.execute("INSERT INTO [ContractLines] ([ContractID], [Kind], [LineNo], [Description], [Note]) VALUES (?, 'مواصفات', ?, ?, ?)", (contract_id, i, n, d))
        for i, (lab, pct, amt, due) in enumerate(tranches, 1):
            tx.execute("INSERT INTO [ContractTranches] ([ContractID], [TrancheNo], [Label], [Pct], [Amount], [DueText]) VALUES (?, ?, ?, ?, ?, ?)", (contract_id, i, lab, pct, amt, due))
        for k, v in custom.items():
            tx.execute("INSERT INTO [ContractData] ([ContractID], [FieldKey], [FieldValue]) VALUES (?, ?, ?)", (contract_id, k, v))
    history(contract_id, "إنشاء مسودة" if created else "تعديل البيانات", tpl["Name"], user)
    return contract_id


# ---------------------------------------------------------------- generating the files
_GENERATING = set()                                   # contracts whose files are being made right now
_GEN_GUARD = threading.Lock()


def generate(cid, user=""):
    """Fills the template -> DOCX (always) + PDF (when a converter exists). Returns {revision, docx, pdf, missing, pdf_error}.
    The Word -> PDF step runs WITHOUT the global write lock (it takes seconds to a minute), so a second click on the same contract must not start a
    second generation (two «revision 5» files): it is refused until the first one has finished."""
    with _GEN_GUARD:
        if cid in _GENERATING:
            raise ContractError("جارٍ إنشاء ملفات هذا العقد الآن؛ انتظر حتى تنتهي (حتى دقيقة) ثم حدّث الصفحة.")
        _GENERATING.add(cid)
    try:
        return _generate(cid, user)
    finally:
        with _GEN_GUARD:
            _GENERATING.discard(cid)


def _generate(cid, user=""):
    d = load(cid)
    if not d or d["c"].get("Origin") != "مولَّد":
        raise ContractError("هذا العقد ليس عقدًا مولَّدًا من قالب.")
    c = d["c"]
    ver = db.query("SELECT * FROM [ContractTemplateVersions] WHERE [VersionID] = ?", (c.get("TemplateVersionID") or 0,))
    tpl_id = ver[0]["TemplateID"] if ver else None
    if c.get("Status") != ST_ACTIVE and tpl_id:                       # until it is signed, a regeneration takes the template's latest version
        latest = templates.current_version(tpl_id)
        if latest:
            ver = [latest]
            db.execute("UPDATE [Contracts] SET [TemplateVersionID] = ? WHERE [ContractID] = ?", (latest["VersionID"], cid))
    if not ver:
        raise ContractError("ملف القالب غير موجود.")
    values, tables = build_values(cid), build_tables(cid)
    rev = (c.get("Revision") or 0) + 1
    label = archive.safe_name("%s - مراجعة %d" % (c.get("ContractNo") or cid, rev), maxlen=60)
    tmp = tempfile.mkdtemp(prefix="po_contract_")
    docx_path, pdf_path = os.path.join(tmp, label + ".docx"), os.path.join(tmp, label + ".pdf")
    try:
        report = contract_render.render(templates.path_of(ver[0]), docx_path, values, tables)
        pdf_error = None
        try:
            with write_lock.released():                        # Word / LibreOffice take seconds to a minute: other people may save meanwhile
                contract_pdf.convert(docx_path, pdf_path)
        except contract_pdf.PdfUnavailable as e:
            pdf_error = str(e)
        folder = archive.ensure_contract_folder(cid)
        with open(docx_path, "rb") as f:
            docx_doc = archive.store_generated("CONTRACT", cid, folder, "عقد DOCX", f.read(), label + ".docx", user)
        pdf_doc = None
        if os.path.isfile(pdf_path):
            with open(pdf_path, "rb") as f:
                pdf_doc = archive.store_generated("CONTRACT", cid, folder, "عقد PDF", f.read(), label + ".pdf", user)
    finally:
        for f in os.listdir(tmp):
            try:
                os.remove(os.path.join(tmp, f))
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass
    status = ST_ISSUED if c.get("Status") == ST_DRAFT else c.get("Status")
    db.execute("UPDATE [Contracts] SET [Revision] = ?, [GeneratedAt] = ?, [GeneratedBy] = ?, [Status] = ? WHERE [ContractID] = ?", (rev, datetime.now(), user, status, cid))
    db.execute("DELETE FROM [ContractData] WHERE [ContractID] = ? AND [FieldKey] = '_missing'", (cid,))
    if report["missing"]:
        db.execute("INSERT INTO [ContractData] ([ContractID], [FieldKey], [FieldValue]) VALUES (?, '_missing', ?)", (cid, "، ".join(report["missing"])))
    history(cid, "إنشاء الملفات", "مراجعة %d%s%s" % (rev, " · ناقص %d حقل" % len(report["missing"]) if report["missing"] else "", " · بدون PDF" if pdf_error else ""), user)
    return {"revision": rev, "docx": docx_doc, "pdf": pdf_doc, "missing": report["missing"], "pdf_error": pdf_error, "unknown_blocks": report["unknown_blocks"]}


def upload_signed(cid, file, user=""):
    """The signed copy goes to the SAME record; an issued generated contract becomes «ساري»."""
    c = db.query("SELECT * FROM [Contracts] WHERE [ContractID] = ?", (cid,))
    if not c:
        raise ContractError("العقد غير موجود.")
    folder = archive.ensure_contract_folder(cid)
    archive.store_document("CONTRACT", cid, folder, "العقد الموقّع", file, file.filename, "", user)
    if c[0].get("Origin") == "مولَّد" and c[0].get("Status") in (ST_DRAFT, ST_ISSUED):
        db.execute("UPDATE [Contracts] SET [Status] = ? WHERE [ContractID] = ?", (ST_ACTIVE, cid))
    history(cid, "رفع النسخة الموقّعة", file.filename, user)


# ---------------------------------------------------------------- helpers for the screens
EXTRA_FIELDS = {"مقاول": {"address": "Address", "email": "Email", "rep_name": "RepName", "rep_title": "RepTitle", "unified_no": "UnifiedNo", "bank_name": "BankName", "account_holder": "AccountHolder"},
                "مورد": {"address": "Address", "rep_title": "RepTitle", "unified_no": "UnifiedNo", "bank_name": "BankName", "account_holder": "AccountHolder"}}


def save_party_extras(kind, pid, form):
    """The contracting data (address, representative, bank ...) typed on the contractor's / supplier's page - only the fields present in the form."""
    cols = EXTRA_FIELDS[kind]
    sets = [(col, (form.get(key) or "").strip()) for key, col in cols.items() if key in form]
    if sets:
        table, idcol = ("Suppliers", "SupplierID") if kind == "مورد" else ("Subcontractors", "SubcontractorID")
        db.execute("UPDATE [%s] SET %s WHERE [%s] = ?" % (table, ", ".join("[%s] = ?" % c for c, _v in sets), idcol), tuple(v for _c, v in sets) + (pid,))


PARTY_FIELD = {"الطرف_الثاني_السجل": "cr", "الطرف_الثاني_الرقم_الوطني": "unified", "الطرف_الثاني_الرقم_الضريبي": "vat", "الطرف_الثاني_العنوان": "address", "الطرف_الثاني_الهاتف": "phone",
               "الطرف_الثاني_البريد": "email", "الطرف_الثاني_الممثل": "rep", "الطرف_الثاني_الصفة": "title", "الطرف_الثاني_البنك": "bank", "الطرف_الثاني_الآيبان": "iban"}


def party_gaps(kind, pid, version):
    """The data of the party that the template uses and the party's record does not have yet (labels) - so it can be completed BEFORE generating."""
    p = party(kind, pid) or {}
    used = {f["key"] for f in templates.fields_of(version).get("fields", [])} if version else set()
    return [V.STANDARD[k][0] for k, attr in PARTY_FIELD.items() if k in used and not (p.get(attr) or "").strip()]


def company_gaps(version):
    comp = company()
    used = {f["key"] for f in templates.fields_of(version).get("fields", [])} if version else set()
    mp = {"الطرف_الأول_السجل": "CR", "الطرف_الأول_الرقم_الضريبي": "VatNo", "الطرف_الأول_العنوان": "Address", "الطرف_الأول_الهاتف": "Phone", "الطرف_الأول_الممثل": "RepName", "الطرف_الأول_الصفة": "RepTitle",
          "الطرف_الأول_البنك": "BankName", "الطرف_الأول_الآيبان": "IBAN", "الطرف_الأول_البريد": "Email"}
    return [V.STANDARD[k][0] for k, col in mp.items() if k in used and not (comp.get(col) or "").strip()]


def sample_render(version):
    """The template filled with demo data (to check a template before using it). Returns the DOCX bytes."""
    info = templates.fields_of(version)
    vals = {k: "«%s»" % lab for k, (lab, _g) in V.STANDARD.items()}
    vals.update({"رقم_العقد": "SC-2026-000", "تاريخ_العقد": "20/10/2026", "يوم_العقد": "الثلاثاء", "نسبة_الضريبة": "15%", "القيمة_بالحروف": "فقط ستة وأربعون ألف ريال سعودي لا غير",
                 "عبارة_القيمة": "فقط ستة وأربعون ألف ريال سعودي لا غير شاملة ضريبة القيمة المضافة.", "طريقة_الاحتساب": "السعر مقطوع وثابت ولا يخضع لإعادة القياس."})
    for f in info.get("fields", []):
        if not f["standard"]:
            vals[f["key"]] = f["options"][0] if f["options"] else "«%s»" % f["label"]
    tables = {"جدول_الأسعار": {"header": ["م", "البند", "الوحدة", "السعر قبل الضريبة (ريال)", "ضريبة القيمة المضافة 15% (ريال)", "السعر شامل الضريبة (ريال)"], "rows": [["1", "بند تجريبي", "مقطوعية", "40,000", "6,000", "46,000"], ["", "الإجمالي", "", "40,000", "6,000", "46,000"]],
                              "widths": [0.06, 0.34, 0.1, 0.17, 0.17, 0.16], "align": ["center", "right", "center", "center", "center", "center"], "bold_last": True},
              "جدول_المواصفات": {"header": ["م", "العمل", "الوصف"], "rows": [["1", "عمل تجريبي", "وصف تجريبي"]], "widths": [0.06, 0.22, 0.72], "align": ["center", "center", "right"]},
              "جدول_الدفعات": {"header": ["الدفعة", "النسبة", "المبلغ قبل الضريبة (ريال)", "موعد / شرط الاستحقاق"], "rows": [["الأولى", "40%", "16,000", "عند التوريد"], ["الثانية", "60%", "24,000", "بعد الاستلام"]],
                               "widths": [0.14, 0.12, 0.2, 0.54], "align": ["center", "center", "center", "right"]}}
    tmp = tempfile.mkdtemp(prefix="po_tpl_test_")
    out = os.path.join(tmp, "test.docx")
    try:
        contract_render.render(templates.path_of(version), out, vals, tables)
        with open(out, "rb") as f:
            return f.read()
    finally:
        for f in os.listdir(tmp):
            os.remove(os.path.join(tmp, f))
        os.rmdir(tmp)


def form_state(cid):
    """The values the wizard shows for an existing contract."""
    d = load(cid)
    c = d["c"]
    return {"party": ("sup:%s" % c["SupplierID"]) if c.get("PartyKind") == "مورد" else ("sub:%s" % c["SubcontractorID"]), "template_version": c.get("TemplateVersionID"), "c": c,
            "price_lines": d["price_lines"], "spec_lines": d["spec_lines"], "tranches": d["tranches"], "data": d["data"]}

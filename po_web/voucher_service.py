# -*- coding: utf-8 -*-
"""
voucher_service.py - the GENERAL PAYMENT VOUCHER («أمر صرف عام» / «طلب صرف»), numbered PR-0027 ..., printed on the company form:
header with the company data, beneficiary, request type, requesting department, cost centre, payment method and statement, the referenced documents,
the items, VAT 15 %, the amount in words and the four signatures.

The voucher is a DOCUMENT: saving or approving it does not post anything to a contractor's or supplier's account (the payment itself is recorded where it
is made: the payment cycle, the direct payment, or the supplier's account). Statuses: مسودة (editable) -> معتمد (locked) ; either can become ملغي.

Money (the company rules):  the lines are added up (each line = qty x price, rounded half up); the VAT is taken from the lines
    «excl» prices WITHOUT VAT:  subtotal = sum · VAT = 15 % of it · total = subtotal + VAT
    «incl» prices WITH VAT   :  total = sum · subtotal = total / 1.15 · VAT = total - subtotal   (an unpaid certificate or purchase order is already «incl»)
the discount is deducted AFTER the VAT (the owner's rule for purchase orders) -> net = total - discount.
"""
import json
from datetime import datetime

import arabic_numbers
import audit
import db
import money_guards as mg
import order_math
from textnorm import clean_spaces, norm_key

ST_DRAFT, ST_APPROVED, ST_CANCELLED = "مسودة", "معتمد", "ملغي"
PAY_METHODS = ["تحويل بنكي", "شيك", "نقداً", "مقاصة / تسوية"]
REF_TYPES = ["فاتورة", "مستخلص", "أمر شراء", "عقد", "عرض سعر", "أخرى"]
MODES = {"excl": "الأسعار غير شاملة الضريبة", "incl": "الأسعار شاملة الضريبة"}
MAX_ITEMS, MAX_REFS = 40, 20
SERIES_KEY = "PR"
EPS = 0.005
SIGN_FIELDS = (("accountant", "المحاسب", "SignAccountant"), ("accounts", "مدير الحسابات", "SignAccounts"),
               ("finance", "المدير المالي", "SignFinance"), ("gm", "المدير العام", "SignGM"))


class VoucherError(Exception):
    """A rule was broken - the message is shown as is."""


def _r2(x):
    return round(float(x or 0), 2)


# ----------------------------------------------------------------------------------------------------------------------------- the money
def compute(items, mode="excl", vat_pct=15.0, discount=0.0):
    """items: [(qty, price)] -> {lines: [line totals], subtotal, vat, total, discount, net}. See the module text for the rules."""
    lines = [order_math.line_total(q, p) for q, p in items]
    summed = _r2(sum(lines))
    pct = float(vat_pct or 0)
    if mode == "incl":
        total = summed
        subtotal = order_math.money2(total / (1 + pct / 100)) if pct else total
        vat = _r2(total - subtotal)
    else:
        subtotal = summed
        vat = order_math.money2(subtotal * pct / 100)
        total = _r2(subtotal + vat)
    discount = _r2(discount)
    return {"lines": lines, "subtotal": subtotal, "vat": vat, "total": total, "discount": discount, "net": _r2(total - discount)}


# ----------------------------------------------------------------------------------------------------------------------------- numbering
def next_no(tx):
    row = tx.query("SELECT * FROM [NumberSeries] WHERE [SeriesKey] = ? AND [SeriesYear] = ?", (SERIES_KEY, 0))
    n = (row[0]["LastNo"] if row else 0) or 0
    taken = {r["VoucherNo"] for r in tx.query("SELECT [VoucherNo] FROM [PaymentVouchers]")}
    while True:
        n += 1
        number = "PR-%04d" % n
        if number not in taken:
            break
    if row:
        tx.execute("UPDATE [NumberSeries] SET [LastNo] = ? WHERE [SeriesKey] = ? AND [SeriesYear] = ?", (n, SERIES_KEY, 0))
    else:
        tx.execute("INSERT INTO [NumberSeries] ([SeriesKey], [SeriesYear], [LastNo]) VALUES (?, ?, ?)", (SERIES_KEY, 0, n))
    return number


def last_number():
    rows = db.query("SELECT [LastNo] FROM [NumberSeries] WHERE [SeriesKey] = ? AND [SeriesYear] = ?", (SERIES_KEY, 0))
    return (rows[0]["LastNo"] if rows else 0) or 0


def set_start_number(first_no):
    """Continue the old system's numbering: the NEXT voucher gets `first_no` (allowed only while there is no voucher yet)."""
    if db.scalar("SELECT COUNT(*) FROM [PaymentVouchers]", default=0):
        raise VoucherError("لا يمكن تغيير بداية الترقيم بعد إنشاء أوامر صرف.")
    first_no = int(first_no)
    if not 1 <= first_no <= 99999:
        raise VoucherError("اكتب رقماً بين 1 و99999.")
    with db.transaction() as tx:
        row = tx.query("SELECT * FROM [NumberSeries] WHERE [SeriesKey] = ? AND [SeriesYear] = ?", (SERIES_KEY, 0))
        if row:
            tx.execute("UPDATE [NumberSeries] SET [LastNo] = ? WHERE [SeriesKey] = ? AND [SeriesYear] = ?", (first_no - 1, SERIES_KEY, 0))
        else:
            tx.execute("INSERT INTO [NumberSeries] ([SeriesKey], [SeriesYear], [LastNo]) VALUES (?, ?, ?)", (SERIES_KEY, 0, first_no - 1))


# ----------------------------------------------------------------------------------------------------------------------------- reading the form
def defaults(company):
    """What a new voucher starts with (the signatory names and the department are set once in «بيانات الشركة»)."""
    c = company or {}
    return {"voucher_date": datetime.now().strftime("%Y-%m-%d"), "project_id": "", "party_kind": "sup", "party_id": "", "request_type": "", "department": c.get("VoucherDept") or "",
            "cost_center": "", "pay_method": PAY_METHODS[0], "pay_statement": "", "description": "", "amount_mode": "incl", "vat_pct": "15", "discount": "",
            "items": [], "refs": [], "notes": "",
            "sign": {k: (c.get(col) or "") for k, _lab, col in SIGN_FIELDS}}


def _line_text(form, name, limit, label, required=False):
    v = clean_spaces(form.get(name))
    if required and not v:
        raise VoucherError("%s مطلوب." % label)
    if len(v) > limit:
        raise VoucherError("%s أطول من المسموح (%d حرفاً)." % (label, limit))
    return v


def parse(form):
    """-> (data, errors). `data` is ready for save(); it is also what the form shows again when there are errors."""
    errors, d = [], {}
    d["voucher_date"] = (form.get("voucher_date") or "").strip()
    try:
        when = datetime.strptime(d["voucher_date"], "%Y-%m-%d")
    except ValueError:
        when = None
        errors.append("اكتب تاريخ أمر الصرف.")
    d["when"] = when
    for key, name, limit, label, req in (("request_type", "request_type", 120, "نوع الطلب", False), ("department", "department", 120, "القسم الطالب", False),
                                         ("cost_center", "cost_center", 255, "مركز التكلفة", False), ("pay_statement", "pay_statement", 255, "بيان السداد", False),
                                         ("description", "description", 255, "البيان", True)):
        try:
            d[key] = _line_text(form, name, limit, label, req)
        except VoucherError as e:
            d[key] = clean_spaces(form.get(name))
            errors.append(str(e))
    d["notes"] = (form.get("notes") or "").strip()[:1000]
    d["pay_method"] = form.get("pay_method") if form.get("pay_method") in PAY_METHODS else ""
    if not d["pay_method"]:
        errors.append("اختر طريقة السداد.")
    d["project_id"] = (form.get("project_id") or "").strip()
    prow = db.query("SELECT [ProjectID], [ProjectName] FROM [Projects] WHERE [ProjectID] = ?", (int(d["project_id"]),)) if d["project_id"].isdigit() else []
    d["project_name"] = prow[0]["ProjectName"] if prow else ""
    if not prow:
        errors.append("اختر اسم المشروع.")
    if not d["cost_center"]:
        d["cost_center"] = d["project_name"]
    d["party_kind"] = form.get("party_kind") if form.get("party_kind") in ("sup", "sub") else ""
    d["party_id"] = (form.get("party_id") or "").strip()
    d["party_name"] = ""
    if d["party_kind"] and d["party_id"].isdigit():
        table, idcol = ("Suppliers", "SupplierID") if d["party_kind"] == "sup" else ("Subcontractors", "SubcontractorID")
        rows = db.query("SELECT [Name] FROM [%s] WHERE [%s] = ?" % (table, idcol), (int(d["party_id"]),))
        d["party_name"] = rows[0]["Name"] if rows else ""
    if not d["party_name"]:
        errors.append("اختر المستفيد (مورد أو مقاول) من القائمة.")
    d["amount_mode"] = form.get("amount_mode") if form.get("amount_mode") in MODES else "incl"
    d["vat_pct"] = form.get("vat_pct") if form.get("vat_pct") in ("15", "0") else "15"
    # items
    descs, qtys, prices = form.getlist("item_desc"), form.getlist("item_qty"), form.getlist("item_price")
    items = []
    for i in range(max(len(descs), len(qtys), len(prices))):
        desc, q, p = clean_spaces(descs[i] if i < len(descs) else ""), (qtys[i] if i < len(qtys) else "").strip(), (prices[i] if i < len(prices) else "").strip()
        if not (desc or p):
            continue
        try:
            price = mg.parse_money(p, allow_zero=False, label="السعر في البند %d" % (len(items) + 1), decimals=2)
            qty = mg.parse_money(q or "1", allow_zero=False, label="الكمية في البند %d" % (len(items) + 1), decimals=4)
        except mg.MoneyError as e:
            errors.append(str(e))
            items.append({"desc": desc, "qty": q, "price": p})
            continue
        if not desc:
            errors.append("اكتب بيان البند %d." % (len(items) + 1))
        if len(desc) > 255:
            errors.append("بيان البند %d أطول من 255 حرفاً." % (len(items) + 1))
        items.append({"desc": desc, "qty": qty, "price": price})
    if len(items) > MAX_ITEMS:
        errors.append("عدد البنود أكثر من %d." % MAX_ITEMS)
    if not items:
        errors.append("أضف بنداً واحداً على الأقل بمبلغه.")
    d["items"] = items
    refs = []
    for t, n in zip(form.getlist("ref_type"), form.getlist("ref_no")):
        n = clean_spaces(n)
        if not n:
            continue
        refs.append({"type": t if t in REF_TYPES else "أخرى", "no": n[:60]})
    if len(refs) > MAX_REFS:
        errors.append("عدد المستندات المرجعية أكثر من %d." % MAX_REFS)
    d["refs"] = refs
    try:
        d["discount"] = mg.parse_money(form.get("discount"), allow_zero=True, label="الخصم")
    except mg.MoneyError as e:
        d["discount"] = form.get("discount") or ""
        errors.append(str(e))
    d["sign"] = {k: clean_spaces(form.get("sign_" + k))[:120] for k, _lab, _col in SIGN_FIELDS}
    if not errors:
        t = compute([(i["qty"], i["price"]) for i in items], d["amount_mode"], float(d["vat_pct"]), d["discount"])
        if t["total"] > mg.MAX_MONEY:
            errors.append("إجمالي أمر الصرف كبير جداً؛ راجع الأرقام.")
        elif t["discount"] > t["total"] + EPS:
            errors.append("الخصم (%s) أكبر من الإجمالي بعد الضريبة (%s)." % ("{:,.2f}".format(t["discount"]), "{:,.2f}".format(t["total"])))
        elif t["net"] <= EPS:
            errors.append("صافي أمر الصرف يجب أن يكون أكبر من صفر.")
        d["totals"] = t
    return d, errors


# ----------------------------------------------------------------------------------------------------------------------------- saving
def _columns(d, totals):
    return {"VoucherDate": d["when"], "ProjectID": int(d["project_id"]), "ProjectName": d["project_name"], "CostCenter": d["cost_center"],
            "PartyKind": "مورد" if d["party_kind"] == "sup" else "مقاول", "SupplierID": int(d["party_id"]) if d["party_kind"] == "sup" else None,
            "SubcontractorID": int(d["party_id"]) if d["party_kind"] == "sub" else None, "PartyName": d["party_name"], "RequestType": d["request_type"] or None,
            "Department": d["department"] or None, "PayMethod": d["pay_method"], "PayStatement": d["pay_statement"] or None, "Description": d["description"],
            "AmountMode": d["amount_mode"], "VatPct": float(d["vat_pct"]), "Subtotal": totals["subtotal"], "VatAmount": totals["vat"], "Total": totals["total"],
            "Discount": totals["discount"], "Net": totals["net"],
            "ItemsJson": json.dumps([dict(i, total=t) for i, t in zip(d["items"], totals["lines"])], ensure_ascii=False),
            "RefsJson": json.dumps(d["refs"], ensure_ascii=False), "SignJson": json.dumps(d["sign"], ensure_ascii=False), "Notes": d["notes"] or None}


def create(d, user):
    totals = d["totals"]
    cols = _columns(d, totals)
    with db.transaction() as tx:
        number = next_no(tx)
        cols.update({"VoucherNo": number, "Status": ST_DRAFT, "CreatedBy": user, "CreatedAt": datetime.now(), "ModifiedAt": datetime.now()})
        names = ", ".join("[%s]" % c for c in cols)
        vid = tx.execute_insert("INSERT INTO [PaymentVouchers] (%s) VALUES (%s)" % (names, ", ".join("?" for _ in cols)), tuple(cols.values()))
    audit.log("voucher_create", "VOUCHER", number, "%s — %s — %s" % (d["party_name"], d["project_name"], "{:,.2f}".format(totals["net"])))
    return vid, number


def update(voucher_id, d, user):
    v = get(voucher_id)
    if not v:
        raise VoucherError("أمر الصرف غير موجود.")
    if v["Status"] != ST_DRAFT:
        raise VoucherError("لا يُعدَّل أمر الصرف إلا وهو «مسودة».")
    cols = _columns(d, d["totals"])
    cols["ModifiedAt"] = datetime.now()
    db.execute("UPDATE [PaymentVouchers] SET %s WHERE [VoucherID] = ?" % ", ".join("[%s] = ?" % c for c in cols), tuple(cols.values()) + (voucher_id,))
    audit.log("voucher_edit", "VOUCHER", v["VoucherNo"], "%s — %s" % (d["party_name"], "{:,.2f}".format(d["totals"]["net"])))


def get(voucher_id):
    rows = db.query("SELECT * FROM [PaymentVouchers] WHERE [VoucherID] = ?", (voucher_id,))
    return rows[0] if rows else None


def listing(status="", q=""):
    rows = sorted(db.query("SELECT * FROM [PaymentVouchers]"), key=lambda r: (r.get("VoucherDate") or datetime.min, r["VoucherID"]), reverse=True)
    if status in (ST_DRAFT, ST_APPROVED, ST_CANCELLED):
        rows = [r for r in rows if r["Status"] == status]
    words = [w for w in norm_key(q).split(" ") if w]
    if words:
        rows = [r for r in rows if all(w in norm_key(" ".join(str(r.get(c) or "") for c in ("VoucherNo", "PartyName", "ProjectName", "Description"))) for w in words)]
    return rows


def approve(voucher_id, user):
    v = get(voucher_id)
    if not v or v["Status"] != ST_DRAFT:
        raise VoucherError("يُعتمد أمر الصرف وهو «مسودة» فقط.")
    db.execute("UPDATE [PaymentVouchers] SET [Status] = ?, [ApprovedBy] = ?, [ApprovedAt] = ?, [ModifiedAt] = ? WHERE [VoucherID] = ?", (ST_APPROVED, user, datetime.now(), datetime.now(), voucher_id))
    audit.log("voucher_approve", "VOUCHER", v["VoucherNo"], "{:,.2f}".format(v["Net"] or 0))


def cancel(voucher_id, user):
    v = get(voucher_id)
    if not v or v["Status"] == ST_CANCELLED:
        raise VoucherError("أمر الصرف غير موجود أو ملغي بالفعل.")
    db.execute("UPDATE [PaymentVouchers] SET [Status] = ?, [CancelledBy] = ?, [CancelledAt] = ?, [ModifiedAt] = ? WHERE [VoucherID] = ?", (ST_CANCELLED, user, datetime.now(), datetime.now(), voucher_id))
    audit.log("voucher_cancel", "VOUCHER", v["VoucherNo"], "")


# ----------------------------------------------------------------------------------------------------------------------------- the lists the form is linked to
def projects():
    return [r for r in db.query("SELECT [ProjectID], [ProjectName], [IsActive] FROM [Projects]") if r.get("IsActive") is not False]


def parties(kind, project_name=""):
    """[{id, name, related}] - every supplier / contractor; `related` = already works on this project (they are listed first)."""
    pk = norm_key(project_name)
    out = []
    if kind == "sup":
        rel = {norm_key(o["SupplierName"]) for o in db.query("SELECT [SupplierName], [Project] FROM [PurchaseOrders]") if pk and norm_key(o.get("Project")) == pk}
        rel_ids = {o["SupplierID"] for o in db.query("SELECT [SupplierID], [Project] FROM [PurchaseOrders]") if pk and o.get("SupplierID") and norm_key(o.get("Project")) == pk}
        for s in db.query("SELECT [SupplierID], [Name] FROM [Suppliers]"):
            out.append({"id": s["SupplierID"], "name": s["Name"], "related": s["SupplierID"] in rel_ids or norm_key(s["Name"]) in rel})
    else:
        import contracts_service as cs
        _subs, contracts, certs, _p = cs.load()
        rel_ids = {g["SubcontractorID"] for g in certs if pk and norm_key(g.get("Project")) == pk} | {c.get("SubcontractorID") for c in contracts.values() if pk and norm_key(c.get("Project")) == pk}
        for s in db.query("SELECT [SubcontractorID], [Name] FROM [Subcontractors]"):
            out.append({"id": s["SubcontractorID"], "name": s["Name"], "related": s["SubcontractorID"] in rel_ids})
    return sorted(out, key=lambda r: (not r["related"], norm_key(r["name"])))


def documents(kind, party_id, project_name=""):
    """The party's unpaid documents on this project, ready to become a voucher line: [{type, no, label, amount}] (amounts INCLUDE the VAT)."""
    pk = norm_key(project_name)
    out = []
    if kind == "sup":
        import supplier_account
        for o in sorted(supplier_account.supplier_orders(int(party_id)), key=lambda o: o.get("OrderDate") or datetime.min, reverse=True):
            if o.get("PaymentStatus") == "تم السداد" or (pk and norm_key(o.get("Project")) != pk) or _r2(o.get("TotalAmount")) <= 0:
                continue
            out.append({"type": "أمر شراء", "no": o["PONumber"], "amount": _r2(o["TotalAmount"]),
                        "label": "أمر شراء %s — %s" % (o["PONumber"], o.get("Project") or ""), "desc": "سداد أمر الشراء %s" % o["PONumber"]})
    else:
        import contracts_service as cs
        _subs, _contracts, certs, _p = cs.load()
        for g in sorted((g for g in certs if g["SubcontractorID"] == int(party_id)), key=lambda g: (g.get("CertificateDate") or datetime.min), reverse=True):
            if g["Status"] == "مرفوض" or g.get("ClosedAt") or g["Remaining"] <= EPS or (pk and norm_key(g.get("Project")) != pk):
                continue
            site = (" — " + g["SiteName"]) if g.get("SiteName") else ""
            out.append({"type": "مستخلص", "no": str(g["CertificateNo"]), "amount": _r2(g["Remaining"]),
                        "label": "مستخلص رقم %s%s — المتبقي" % (g["CertificateNo"], site), "desc": "سداد مستخلص رقم %s%s" % (g["CertificateNo"], site)})
    return out


# ----------------------------------------------------------------------------------------------------------------------------- printing
def print_context(v, company):
    """Everything the printed form needs, from the SAVED voucher (the figures are the stored ones, never recomputed differently)."""
    items = json.loads(v.get("ItemsJson") or "[]")
    refs = json.loads(v.get("RefsJson") or "[]")
    sign = json.loads(v.get("SignJson") or "{}")
    return {"v": v, "items": items, "refs": refs, "words": arabic_numbers.amount_to_words(v.get("Net") or 0),
            "signs": [{"title": lab, "name": sign.get(k) or ""} for k, lab, _col in SIGN_FIELDS], "company": company or {}, "printed_at": datetime.now().strftime("%H:%M:%S ,%d/%m/%Y")}

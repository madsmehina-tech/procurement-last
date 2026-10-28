# -*- coding: utf-8 -*-
"""
supplier_account.py - the SUPPLIER's account statement (كشف حساب المورد), laid out exactly like the contractor's:

    مدين  (debit)  = what the company OWES the supplier: every purchase order, and other dues (استحقاق آخر، رد دفعة)
    دائن  (credit) = what was PAID or deducted: payments, returns / credit notes, deductions
    الرصيد = credit - debit      negative: the supplier still has dues from us · positive: we paid more than due · zero: settled

Where the numbers come from (nothing is copied, so nothing can drift):
  * purchases  -> PurchaseOrders of this supplier (matched by SupplierID, or by the normalised name for older orders). Basis «orders»: every order at its
                  order date; basis «invoiced»: only orders whose invoice was issued, at the invoice date.
  * payments   -> the movements typed on the supplier's page (SupplierEntries). An order marked «تم السداد» whose payment was NOT typed by hand is counted as
                  paid in full on its payment date; if a typed payment names that order, the typed amount is used instead (partial payments, other amounts).
  * opening    -> the opening balance (opening_service): purchases dated before its date are already inside it; payments dated after it still count.
"""
from datetime import datetime, timedelta

import db
import money_guards as mg
import opening_service
from opening_service import CAT_OPENING
from textnorm import clean_spaces, norm_key

K_CREDIT, K_DEBIT = "دائن", "مدين"
CAT_PAYMENT = "دفعة"
CATEGORIES = {K_CREDIT: [CAT_PAYMENT, "مرتجع / إشعار دائن", "خصم مكتسب", "تسوية أخرى"], K_DEBIT: ["استحقاق آخر", "رد دفعة"]}
EPS = 0.005
PAID, INVOICED = "تم السداد", "تم الإصدار"


class SupplierAccountError(Exception):
    """A rule was broken - the message is shown as is."""


def _r2(x):
    return round(float(x or 0), 2)


def _dt(d):
    return d if isinstance(d, datetime) else datetime.min


# ----------------------------------------------------------------------------------------------------------------------------- movements
def entries(supplier_id):
    try:
        rows = db.query("SELECT * FROM [SupplierEntries] WHERE [SupplierID] = ?", (supplier_id,))
    except Exception:                                    # noqa - before the migration the table does not exist
        return []
    return sorted(rows, key=lambda r: (r.get("EntryDate") or datetime.min, r["EntryID"]), reverse=True)


def supplier_orders(supplier_id, name=None):
    """The purchase orders that belong to this supplier."""
    if name is None:
        rows = db.query("SELECT [Name] FROM [Suppliers] WHERE [SupplierID] = ?", (supplier_id,))
        name = rows[0]["Name"] if rows else ""
    key = norm_key(name)
    return [o for o in db.query("SELECT * FROM [PurchaseOrders]") if o.get("SupplierID") == supplier_id or (key and norm_key(o.get("SupplierName")) == key)]


def add_entry(supplier_id, kind, category, description, amount, when, project, po_number, bank_ref, user):
    kind, category, description = (kind or "").strip(), (category or "").strip(), clean_spaces(description)
    if kind not in CATEGORIES or category not in CATEGORIES[kind]:
        raise SupplierAccountError("اختر النوع (دائن / مدين) والتصنيف.")
    if not description:
        raise SupplierAccountError("اكتب وصف الحركة.")
    amount = _r2(amount)
    if amount <= 0:
        raise SupplierAccountError("المبلغ يجب أن يكون أكبر من صفر.")
    when = when or datetime.now()
    try:
        opening_service.guard_supplier_date(supplier_id, when, "هذه الحركة")
    except opening_service.OpeningError as e:
        raise SupplierAccountError(str(e))
    po_number = clean_spaces(po_number)
    if po_number and not any(o["PONumber"] == po_number for o in supplier_orders(supplier_id)):
        raise SupplierAccountError("أمر الشراء %s ليس من أوامر هذا المورد." % po_number)
    return db.execute_insert(
        "INSERT INTO [SupplierEntries] ([SupplierID], [Kind], [Category], [Description], [Amount], [EntryDate], [ProjectName], [PONumber], [BankRef], [CreatedBy], [CreatedAt]) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (supplier_id, kind, category, description[:250], amount, when, clean_spaces(project) or None, po_number or None, (bank_ref or "")[:80], user, datetime.now()))


def delete_entry(entry_id):
    rows = db.query("SELECT * FROM [SupplierEntries] WHERE [EntryID] = ?", (entry_id,))
    if not rows:
        raise SupplierAccountError("الحركة غير موجودة.")
    if rows[0].get("Category") == CAT_OPENING:
        raise SupplierAccountError("هذا «رصيد الافتتاح»؛ يُعدَّل أو يُلغى من بطاقة «رصيد الافتتاح» لا من قائمة الحركات.")
    db.execute("DELETE FROM [SupplierEntries] WHERE [EntryID] = ?", (entry_id,))
    return rows[0]["SupplierID"]


# ----------------------------------------------------------------------------------------------------------------------------- the statement
def statement(supplier_id, date_from=None, date_to=None, project="", basis="orders"):
    """{supplier, lines, opening (balance before date_from), debit, credit, balance, note, purchases, paid, opening_entry, projects}.
    Every line: seq, date, desc, debit, credit, balance, kind (po / po_paid / entry / opening)."""
    srows = db.query("SELECT * FROM [Suppliers] WHERE [SupplierID] = ?", (supplier_id,))
    if not srows:
        return {}
    sup = srows[0]
    pk = norm_key(project)
    op = opening_service.supplier_opening(supplier_id)
    op_date = op["date"].date() if op and op.get("date") else None
    typed = entries(supplier_id)
    typed_po = {e["PONumber"] for e in typed if e.get("PONumber") and e["Kind"] == K_CREDIT}
    items = []

    def add(date, desc, debit, credit, project_name, kind):
        items.append({"date": date or datetime.min, "desc": desc, "debit": _r2(debit), "credit": _r2(credit), "project": project_name or "", "kind": kind})

    for o in supplier_orders(supplier_id, sup["Name"]):
        total = _r2(o.get("TotalAmount"))
        invoiced = o.get("InvoiceStatus") == INVOICED
        if basis == "invoiced":
            buy_date = o.get("InvoiceDate") or o.get("OrderDate") or o.get("CreatedAt")
            buys = invoiced
        else:
            buy_date = o.get("OrderDate") or o.get("CreatedAt")
            buys = True
        if buys and total > 0 and not (op_date and _dt(buy_date).date() < op_date):
            add(buy_date, "أمر شراء %s%s" % (o["PONumber"], (" — " + o["Project"]) if o.get("Project") else "") + (" (فاتورة)" if basis == "invoiced" else ""), total, 0, o.get("Project"), "po")
        if o.get("PaymentStatus") == PAID and total > 0 and o["PONumber"] not in typed_po:
            pay_date = o.get("PaymentDate") or o.get("OrderDate") or o.get("CreatedAt")
            if not (op_date and _dt(pay_date).date() < op_date):
                add(pay_date, "سداد أمر الشراء %s (كامل قيمته)" % o["PONumber"], 0, total, o.get("Project"), "po_paid")
    for e in typed:
        d = e.get("EntryDate")
        kind = "opening" if e["Category"] == CAT_OPENING else "entry"
        extra = (" — أمر %s" % e["PONumber"]) if e.get("PONumber") else ""
        add(d, "%s — %s%s" % (e["Category"], e["Description"], extra) if kind == "entry" else e["Description"],
            e["Amount"] if e["Kind"] == K_DEBIT else 0, e["Amount"] if e["Kind"] == K_CREDIT else 0, e.get("ProjectName"), kind)
    if pk:                                                # a project view shows only what belongs to that project (the opening balance belongs to none)
        items = [i for i in items if norm_key(i["project"]) == pk]
    items.sort(key=lambda i: (i["date"], i["kind"] != "opening", i["desc"]))
    previous = _r2(sum(i["credit"] - i["debit"] for i in items if date_from and i["date"] < date_from))
    end = (date_to + timedelta(days=1)) if date_to else None
    shown = [i for i in items if (not date_from or i["date"] >= date_from) and (not end or i["date"] < end)]
    lines, running = [], previous
    for n, i in enumerate(shown, 1):
        running = _r2(running + i["credit"] - i["debit"])
        lines.append(dict(i, seq=n, balance=running))
    debit, credit = _r2(sum(i["debit"] for i in shown)), _r2(sum(i["credit"] for i in shown))
    balance = _r2(previous + credit - debit)
    note = "للمورد مستحقات علينا" if balance < -EPS else ("دفعنا للمورد أكثر من المستحق" if balance > EPS else "لا توجد فروقات")
    projects = sorted({o.get("Project") for o in supplier_orders(supplier_id, sup["Name"]) if o.get("Project")} | {e["ProjectName"] for e in typed if e.get("ProjectName")}, key=norm_key)
    return {"supplier": sup, "lines": lines, "opening": previous, "debit": debit, "credit": credit, "balance": balance, "note": note,
            "purchases": _r2(sum(i["debit"] for i in shown if i["kind"] == "po")), "paid": _r2(sum(i["credit"] for i in shown if i["kind"] in ("po_paid", "entry"))),
            "opening_entry": op, "projects": projects}


def balance_of(supplier_id):
    s = statement(supplier_id)
    return s["balance"] if s else 0.0

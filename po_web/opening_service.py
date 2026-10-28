# -*- coding: utf-8 -*-
"""
opening_service.py - the OPENING BALANCE of a contractor or a supplier (what was owed when the real work started in this system).

    «مستحق للطرف»  (we owe him)        -> a DEBIT line (مدين) in his statement: it raises what he is due
    «مستحق علينا»  (he owes us)        -> a CREDIT line (دائن): it lowers what he is due

One opening line per party, dated «as of». It is a normal movement of the account (a LedgerEntries row for a contractor, a SupplierEntries row for a
supplier), so the statement, the contractor page, the dashboards and the reports all include it WITHOUT any special case - the figures cannot disagree.

Everything dated BEFORE the as-of date is already inside the opening balance. New certificates / manual movements / purchase orders dated before it are
therefore refused (guard_*), so the same money is never counted twice.
"""
from datetime import datetime

import audit
import db
import ledger_service as ls
from textnorm import clean_spaces

CAT_OPENING = "رصيد افتتاحي"
DIR_OWE_PARTY, DIR_PARTY_OWES = "party", "us"            # who owes whom
LABEL = {DIR_OWE_PARTY: "مستحق للطرف (نحن مدينون له)", DIR_PARTY_OWES: "مستحق علينا (هو مدين لنا)"}


class OpeningError(ValueError):
    """A rule was broken - the message is shown as is."""


def _r2(x):
    return round(float(x or 0), 2)


def _dir_of(kind):
    return DIR_OWE_PARTY if kind == ls.K_DEBIT else DIR_PARTY_OWES


def _kind_of(direction):
    if direction not in (DIR_OWE_PARTY, DIR_PARTY_OWES):
        raise OpeningError("اختر اتجاه الرصيد: مستحق للطرف أم مستحق علينا.")
    return ls.K_DEBIT if direction == DIR_OWE_PARTY else ls.K_CREDIT


def _row(r):
    return {"id": r["EntryID"], "amount": _r2(r["Amount"]), "direction": _dir_of(r["Kind"]), "label": LABEL[_dir_of(r["Kind"])], "date": r.get("EntryDate"),
            "note": r.get("Description") or "", "by": r.get("CreatedBy") or ""}


def signed(opening):
    """+ = the company owes the party · - = the party owes the company (0 when there is no opening balance)."""
    if not opening:
        return 0.0
    return opening["amount"] if opening["direction"] == DIR_OWE_PARTY else -opening["amount"]


def _text(when, note):
    base = "رصيد افتتاحي كما في %s" % when.strftime("%Y-%m-%d")
    note = clean_spaces(note)
    return (base + (" — " + note if note else ""))[:250]


# ----------------------------------------------------------------------------------------------------------------------------- contractors
def sub_opening(sub_id):
    try:
        rows = db.query("SELECT * FROM [LedgerEntries] WHERE [SubcontractorID] = ? AND [Category] = ?", (sub_id, CAT_OPENING))
    except Exception:                                       # noqa - before the ledger migration there is nothing
        return None
    return _row(rows[0]) if rows else None


def set_sub_opening(sub_id, direction, amount, when, note, user):
    kind, amount = _kind_of(direction), _r2(amount)
    if amount <= 0:
        raise OpeningError("مبلغ رصيد الافتتاح يجب أن يكون أكبر من صفر (لإلغائه استخدم «إلغاء الرصيد الافتتاحي»).")
    if not isinstance(when, datetime):
        raise OpeningError("اكتب تاريخ رصيد الافتتاح (الرصيد كما في هذا التاريخ).")
    before = sub_opening(sub_id)
    with db.transaction() as tx:
        tx.execute("DELETE FROM [LedgerEntries] WHERE [SubcontractorID] = ? AND [Category] = ?", (sub_id, CAT_OPENING))
        tx.execute_insert(
            "INSERT INTO [LedgerEntries] ([SubcontractorID], [Kind], [Category], [Description], [Amount], [EntryDate], [Status], [ExecAt], [CreatedBy], [CreatedAt]) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (sub_id, kind, CAT_OPENING, _text(when, note), amount, when, ls.S_DONE, datetime.now(), user, datetime.now()))
    audit.log("opening_balance_set", "SUB", sub_id, "%s %s%s" % (LABEL[direction], "{:,.2f}".format(amount), (" (كان %s)" % "{:,.2f}".format(before["amount"])) if before else ""))


def clear_sub_opening(sub_id):
    db.execute("DELETE FROM [LedgerEntries] WHERE [SubcontractorID] = ? AND [Category] = ?", (sub_id, CAT_OPENING))
    audit.log("opening_balance_clear", "SUB", sub_id, "")


# ----------------------------------------------------------------------------------------------------------------------------- suppliers
def supplier_opening(supplier_id):
    try:
        rows = db.query("SELECT * FROM [SupplierEntries] WHERE [SupplierID] = ? AND [Category] = ?", (supplier_id, CAT_OPENING))
    except Exception:                                       # noqa
        return None
    return _row(rows[0]) if rows else None


def set_supplier_opening(supplier_id, direction, amount, when, note, user):
    kind, amount = _kind_of(direction), _r2(amount)
    if amount <= 0:
        raise OpeningError("مبلغ رصيد الافتتاح يجب أن يكون أكبر من صفر (لإلغائه استخدم «إلغاء الرصيد الافتتاحي»).")
    if not isinstance(when, datetime):
        raise OpeningError("اكتب تاريخ رصيد الافتتاح (الرصيد كما في هذا التاريخ).")
    before = supplier_opening(supplier_id)
    with db.transaction() as tx:
        tx.execute("DELETE FROM [SupplierEntries] WHERE [SupplierID] = ? AND [Category] = ?", (supplier_id, CAT_OPENING))
        tx.execute_insert(
            "INSERT INTO [SupplierEntries] ([SupplierID], [Kind], [Category], [Description], [Amount], [EntryDate], [CreatedBy], [CreatedAt]) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (supplier_id, kind, CAT_OPENING, _text(when, note), amount, when, user, datetime.now()))
    audit.log("opening_balance_set", "SUPPLIER", supplier_id, "%s %s%s" % (LABEL[direction], "{:,.2f}".format(amount), (" (كان %s)" % "{:,.2f}".format(before["amount"])) if before else ""))


def clear_supplier_opening(supplier_id):
    db.execute("DELETE FROM [SupplierEntries] WHERE [SupplierID] = ? AND [Category] = ?", (supplier_id, CAT_OPENING))
    audit.log("opening_balance_clear", "SUPPLIER", supplier_id, "")


# ----------------------------------------------------------------------------------------------------------------------------- the guard
def _as_date(d):
    if d is None:
        return None
    return d.date() if hasattr(d, "date") else d


def _guard(opening, when, what):
    if not opening or not opening.get("date") or when is None:
        return
    if _as_date(when) < _as_date(opening["date"]):
        raise OpeningError("تاريخ %s (%s) يسبق تاريخ رصيد الافتتاح (%s): ما قبل هذا التاريخ مشمول في رصيد الافتتاح ولا يُدخل مرة ثانية. "
                           "إن كان تاريخك صحيحاً فعدّل تاريخ رصيد الافتتاح أولاً." % (what, _as_date(when).strftime("%Y-%m-%d"), _as_date(opening["date"]).strftime("%Y-%m-%d")))


def guard_sub_date(sub_id, when, what="هذه الحركة"):
    _guard(sub_opening(sub_id), when, what)


def guard_supplier_date(supplier_id, when, what="هذه الحركة"):
    _guard(supplier_opening(supplier_id), when, what)


# ----------------------------------------------------------------------------------------------------------------------------- the bulk page
def all_parties():
    """[{kind, id, name, opening}] every contractor and supplier with the opening balance they have now - for the bulk entry page."""
    out = []
    for s in db.query("SELECT [SubcontractorID], [Name] FROM [Subcontractors] ORDER BY [Name]"):
        out.append({"kind": "sub", "id": s["SubcontractorID"], "name": s["Name"], "opening": sub_opening(s["SubcontractorID"])})
    try:
        for s in db.query("SELECT [SupplierID], [Name] FROM [Suppliers] ORDER BY [Name]"):
            out.append({"kind": "sup", "id": s["SupplierID"], "name": s["Name"], "opening": supplier_opening(s["SupplierID"])})
    except Exception:                                       # noqa
        pass
    return out


# ----------------------------------------------------------------------------------------------------------------------------- «new party» forms
def from_form(kind, party_id, form, user):
    """The optional opening-balance fields of the «new contractor / supplier» form. Returns '' (nothing typed / saved) or a message to show.
    The party itself is already saved when this runs: a mistake here never loses it."""
    import money_guards as mg
    raw = (form.get("opening_amount") or "").strip()
    if not raw:
        return ""
    try:
        amount = mg.parse_money(raw, allow_zero=False, label="مبلغ رصيد الافتتاح")
        when = datetime.strptime((form.get("opening_as_of") or "").strip(), "%Y-%m-%d")
    except mg.MoneyError as e:
        return "لم يُحفظ رصيد الافتتاح: %s" % e
    except ValueError:
        return "لم يُحفظ رصيد الافتتاح: اكتب تاريخه (الرصيد كما في هذا التاريخ)."
    try:
        (set_sub_opening if kind == "sub" else set_supplier_opening)(party_id, form.get("opening_direction") or DIR_OWE_PARTY, amount, when, form.get("opening_note"), user)
    except OpeningError as e:
        return "لم يُحفظ رصيد الافتتاح: %s" % e
    return "وسُجّل رصيد الافتتاح."

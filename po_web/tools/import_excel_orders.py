# -*- coding: utf-8 -*-
r"""
python tools\import_excel_orders.py  PO_Tracker.xlsm                  PREVIEW only: nothing is written
python tools\import_excel_orders.py  PO_Tracker.xlsm  --apply --yes   writes what the preview showed

Completes the system from the old Excel tracker - and touches nothing else:
  * an order that is in the Excel sheet «متابعة أوامر الشراء» but NOT in the system is added with its items (sheet «الأصناف»), its receiver
    (sheet «_po_hdr») and its three statuses. Its total is the one written in Excel.
  * an order that IS in the system but has no items there gets its items from Excel. Its total and statuses are NOT changed (the system is the reference),
    and a difference between the items and the total is only reported.
  * every other order is left exactly as it is. Running the tool twice adds nothing the second time.
--only "PO-2026-445,PO-2026-446" limits the work to the numbers you list.
Take a backup before --apply. The PDF files of the added orders are then copied with tools\import_legacy_pdfs.py.
"""
import argparse
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SHEET_ORDERS, SHEET_ITEMS, SHEET_HDR = "متابعة أوامر الشراء", "الأصناف", "_po_hdr"
VAT = 1.15


def norm_po(text):
    return re.sub(r"[^0-9a-z؀-ۿ]", "", str(text or "").lower())


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _txt(v):
    return re.sub(r"[ \t]+", " ", str(v if v is not None else "")).strip()


def read_excel(path):
    """{normalised number: {"po", "date", "supplier", "quote", "project", "total", "receipt", "invoice", "payment", "receiver", "phone", "items": [...]}}  in sheet order."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True, keep_vba=False)
    for name in (SHEET_ORDERS, SHEET_ITEMS):
        if name not in wb.sheetnames:
            raise SystemExit("الملف ليس ملف المتابعة القديم: لا توجد فيه الورقة «%s»." % name)
    orders, started = {}, False
    for r in wb[SHEET_ORDERS].iter_rows(values_only=True):
        r = list(r) + [None] * 11
        if not started:
            started = _txt(r[1]) == "أمر الشراء"
            continue
        if not _txt(r[1]) or not isinstance(r[2], datetime):
            continue
        orders[norm_po(r[1])] = {"po": _txt(r[1]), "date": r[2], "supplier": _txt(r[3]), "quote": _txt(r[4]), "project": _txt(r[5]), "total": round(_num(r[6]), 2),
                                 "receipt": _txt(r[7]) or "معلق", "invoice": _txt(r[8]) or "معلق", "payment": _txt(r[9]) or "معلق", "receiver": "", "phone": "", "items": []}
    started = False
    for r in wb[SHEET_ITEMS].iter_rows(values_only=True):
        r = list(r) + [None] * 9
        if not started:
            started = _txt(r[1]) == "اسم الصنف"
            continue
        o = orders.get(norm_po(r[5]))
        if o is not None and _txt(r[1]):
            o["items"].append({"name": str(r[1]).strip(), "price": _num(r[2]), "qty": _num(r[4]), "unit": _txt(r[7])})
    if SHEET_HDR in wb.sheetnames:
        for r in wb[SHEET_HDR].iter_rows(min_row=2, values_only=True):
            o = orders.get(norm_po(r[0])) if r and r[0] else None
            if o is not None:
                o["receiver"], o["phone"] = _txt(r[1]), _txt(r[2])
    return orders


def plan(orders, only=None):
    """What would be done: {"add": [order...], "items": [(system number, order)...], "notes": [text...], "same": n}"""
    import db
    system = {norm_po(r["PONumber"]): r for r in db.query("SELECT [PONumber], [TotalAmount] FROM [PurchaseOrders]")}
    have_items = {norm_po(r["PONumber"]) for r in db.query("SELECT [PONumber] FROM [OrderItems]")}
    wanted = {norm_po(x) for x in only} if only else None
    out = {"add": [], "items": [], "notes": [], "same": 0}
    for key, o in orders.items():
        if wanted is not None and key not in wanted:
            continue
        lines = round(sum(i["qty"] * i["price"] for i in o["items"]), 2)
        if key not in system:
            if not o["supplier"] or not o["project"]:
                out["notes"].append("%s: تُرك - بلا مورد أو بلا مشروع في الإكسل." % o["po"])
                continue
            out["add"].append(o)
            if not o["items"]:
                out["notes"].append("%s: لا أصناف له في الإكسل - يُضاف برأسه وإجماليه فقط." % o["po"])
            elif abs(lines * VAT - o["total"]) > 1 and abs(lines - o["total"]) > 1:
                out["notes"].append("%s: مجموع أصنافه %.2f (مع الضريبة %.2f) لا يطابق إجماليه في الإكسل %.2f - يُضاف بإجمالي الإكسل." % (o["po"], lines, lines * VAT, o["total"]))
        elif key not in have_items and o["items"]:
            row = system[key]
            out["items"].append((row["PONumber"], o))
            total = float(row["TotalAmount"] or 0)
            if abs(lines * VAT - total) > 1 and abs(lines - total) > 1:
                out["notes"].append("%s: إجماليه في النظام %.2f يبقى كما هو؛ أصناف الإكسل مجموعها %.2f (مع الضريبة %.2f)." % (row["PONumber"], total, lines, lines * VAT))
        else:
            out["same"] += 1
    if wanted:
        for miss in sorted(wanted - set(orders)):
            out["notes"].append("الرقم «%s» غير موجود في ورقة المتابعة بالإكسل." % miss)
    return out


def _insert_items(tx, po_number, items):
    for i, it in enumerate(items, 1):
        tx.execute("INSERT INTO [OrderItems] ([PONumber], [LineNo], [ItemName], [Unit], [Qty], [UnitPrice], [QtyReceived]) VALUES (?, ?, ?, ?, ?, ?, 0)",
                   (po_number, i, it["name"], it["unit"], it["qty"], it["price"]))


def apply(p):
    """Each order is one transaction: the order with ALL its items, or nothing. Returns (added, completed)."""
    import db
    import masterdata
    from order_services import hijri_display
    now, added, completed = datetime.now(), 0, 0
    for o in p["add"]:
        lines = round(sum(i["qty"] * i["price"] for i in o["items"]), 2)
        mode = "none" if o["items"] and abs(lines - o["total"]) <= 1 else "add"
        with db.transaction() as tx:
            sup_id, supplier, _ = masterdata.resolve_supplier(o["supplier"], tx)
            proj_id, project, _ = masterdata.resolve_project(o["project"], tx)
            tx.execute("INSERT INTO [PurchaseOrders] ([PONumber], [OrderDate], [HijriDate], [SupplierName], [QuoteNo], [Project], [Receiver], [ReceiverPhone], [TotalAmount], [Discount], "
                       "[ReceiptStatus], [InvoiceStatus], [PaymentStatus], [CreatedAt], [ModifiedAt], [SupplierID], [ProjectID], [VatMode], [VatPct], [CurrencyCode], [ExchangeRate], [TotalCurrency]) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, 15, 'SAR', 1, ?)",
                       (o["po"], o["date"], hijri_display(o["date"]), supplier, o["quote"], project, o["receiver"], o["phone"], o["total"],
                        o["receipt"], o["invoice"], o["payment"], now, now, sup_id, proj_id, mode, o["total"]))
            _insert_items(tx, o["po"], o["items"])
        added += 1
    for po_number, o in p["items"]:
        with db.transaction() as tx:
            _insert_items(tx, po_number, o["items"])
        completed += 1
    return added, completed


def report(p, out=print):
    out("أوامر في الإكسل وليست في النظام (تُضاف): %d" % len(p["add"]))
    for o in p["add"]:
        out("   + %-14s %s | %s | %s | %d صنف | %.2f ريال" % (o["po"], o["date"].strftime("%Y-%m-%d"), o["supplier"], o["project"], len(o["items"]), o["total"]))
    out("أوامر في النظام بلا أصناف (تُضاف أصنافها فقط، إجماليها لا يتغير): %d" % len(p["items"]))
    for po_number, o in p["items"]:
        out("   ~ %-14s %d صنف" % (po_number, len(o["items"])))
    out("أوامر مطابقة لا تُمس: %d" % p["same"])
    for n in p["notes"]:
        out("   ! " + n)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--only", default="")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--yes", action="store_true")
    a = ap.parse_args(argv)
    if not os.path.isfile(a.xlsx):
        raise SystemExit("الملف غير موجود: %s" % a.xlsx)
    p = plan(read_excel(a.xlsx), [x for x in a.only.split(",") if x.strip()] or None)
    report(p)
    if not a.apply:
        print("\nهذه معاينة فقط: لم يُكتب شيء. للتنفيذ أضف:  --apply --yes")
        return 0
    if not (p["add"] or p["items"]):
        print("\nلا شيء يُضاف.")
        return 0
    if not a.yes and input("\nاكتب «نعم» للتنفيذ: ").strip() != "نعم":
        print("أُلغي.")
        return 1
    added, completed = apply(p)
    print("\nتم فعلاً: أُضيف %d أمر، واكتملت أصناف %d أمر." % (added, completed))
    return 0


if __name__ == "__main__":
    sys.exit(main())

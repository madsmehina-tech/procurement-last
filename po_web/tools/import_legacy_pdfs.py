# -*- coding: utf-8 -*-
r"""
python tools\import_legacy_pdfs.py  PO_Tracker.xlsm  --map "\\192.168.100.55\Purchases Folder Shared=D:\Purchases Folder Shared"  [--search "D:\Purchases Folder Shared\0-PO Tracker"]
        PREVIEW only: nothing is written. Shows, per file type, how many files will be copied, which are already in the system, which cannot be found, and which order is not in the system yet.
python tools\import_legacy_pdfs.py  PO_Tracker.xlsm  --map ... --apply --yes      COPIES the files into the new archive (the old files stay where they are)

What it does for every row of the sheet «المرفقات» of the old tracker (type QU / PO / DN / INV / PR + the order number + the path of the file):
  * finds the order in the system (the numbers are compared ignoring case, dashes and spaces: «po-2026-383» = «po-2026383»);
  * finds the file: the old path after applying --map, else (--search) a file with the same saved name under the folders you give;
  * copies it into the order's own folder of the new archive with archive.store_document (the same code the upload screen uses: the PDF structure is checked,
    the record is written in the Documents table, a name that exists is never overwritten);
  * skips a file that is already attached to that order (same content), so running it twice does not duplicate anything.
It never deletes or moves an old file, and never changes an order. An order that is not in the system is only reported (import the order first).
Stop the server first (or at least make sure nobody is saving) and take a backup before --apply.
"""
import argparse
import csv
import hashlib
import os
import re
import sys
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TYPE_MAP = {"PO": "أمر الشراء", "QU": "عرض السعر", "DN": "سند الاستلام", "INV": "الفاتورة", "PR": "سند السداد"}
TYPE_NAME = {"PO": "أمر شراء", "QU": "عرض سعر", "DN": "سند استلام", "INV": "فاتورة", "PR": "سداد"}


def norm_po(text):
    """po-2026-383 / PO 2026383 / po-2026383  ->  po2026383"""
    return re.sub(r"[^0-9a-z\u0600-\u06ff]", "", str(text or "").lower())


def read_rows(xlsx_path):
    """The rows of the sheet «المرفقات»: dicts with id, type, po, original, saved, path, added."""
    import openpyxl
    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True, keep_vba=False)
    ws = wb["المرفقات"]
    rows = []
    for r in ws.iter_rows(min_row=1, values_only=True):
        if not r or not r[0] or r[0] == "معرف المرفق" or not r[1] or str(r[1]).strip().upper() not in TYPE_MAP:
            continue
        rows.append({"id": str(r[0]), "type": str(r[1]).strip().upper(), "po": str(r[2] or "").strip(), "original": str(r[3] or ""), "saved": str(r[4] or ""),
                     "path": str(r[5] or ""), "added": r[6] if len(r) > 6 else None})
    return rows


def parse_maps(items):
    out = []
    for it in items or []:
        if "=" not in it:
            raise SystemExit("--map يكتب هكذا:  \"الأصل=الجديد\"  (مثال: \\\\192.168.100.55\\Share=D:\\Share)")
        a, b = it.split("=", 1)
        out.append((a.strip().rstrip("\\/").lower(), b.strip().rstrip("\\/")))
    return out


def apply_maps(path, maps):
    p = (path or "").strip()
    low = p.lower().replace("/", "\\")
    for old, new in maps:
        if low.startswith(old.replace("/", "\\") + "\\") or low == old.replace("/", "\\"):
            return os.path.join(new, p[len(old):].lstrip("\\/"))
    return p


def index_files(roots):
    """name (lower) -> [paths] of every pdf under the search roots."""
    idx = {}
    for root in roots or []:
        for d, _dirs, files in os.walk(root):
            for f in files:
                if f.lower().endswith(".pdf"):
                    idx.setdefault(f.lower(), []).append(os.path.join(d, f))
    return idx


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def plan(rows, maps, search_index, orders, existing_hashes):
    """One decision per row. orders = {normalized number: [real PONumber, ...]}; existing_hashes(po) -> set of sha256 already attached to that order."""
    out = []
    seen_in_run = set()
    for r in rows:
        d = dict(r, status="", po_real="", src="", note="")
        exact = [real for real in orders.get("__exact__", {}).get(str(r["po"]).strip().lower(), [])]
        cands = exact or orders.get(norm_po(r["po"]), [])          # the sheet stores the system's own order key: an EXACT match (ignoring case) wins; only then the loose one
        if not cands:
            d["status"], d["note"] = "order_missing", "الأمر غير موجود في النظام: %s" % r["po"]
            out.append(d)
            continue
        if len(cands) > 1:
            d["status"], d["note"] = "order_ambiguous", "أكثر من أمر بالرقم نفسه: %s" % ", ".join(cands)
            out.append(d)
            continue
        d["po_real"] = cands[0]
        src = apply_maps(r["path"], maps)
        if not (src and os.path.isfile(src)):
            alt = [p for n in {r["saved"].lower(), r["original"].lower()} if n for p in search_index.get(n, [])]
            src = alt[0] if len(alt) >= 1 else ""
        if not src:
            d["status"], d["note"] = "file_missing", "الملف غير موجود: %s" % r["path"]
            out.append(d)
            continue
        d["src"] = src
        try:
            h = sha256_file(src)
        except OSError as e:
            d["status"], d["note"] = "file_unreadable", str(e)
            out.append(d)
            continue
        d["sha"] = h
        key = (d["po_real"], TYPE_MAP[r["type"]], h)
        if h in existing_hashes(d["po_real"]) or key in seen_in_run:
            d["status"], d["note"] = "already_there", "موجود في النظام (المحتوى نفسه)"
        else:
            d["status"], d["note"] = "will_copy", ""
            seen_in_run.add(key)
        out.append(d)
    return out


def load_orders():
    import db
    idx = {"__exact__": {}}
    for o in db.query("SELECT [PONumber] FROM [PurchaseOrders]"):
        idx.setdefault(norm_po(o["PONumber"]), []).append(o["PONumber"])
        idx["__exact__"].setdefault(str(o["PONumber"]).strip().lower(), []).append(o["PONumber"])
    return idx


def hashes_of_order(po):
    import archive
    import db
    out = set()
    for d in db.query("SELECT [RelPath] FROM [Documents] WHERE [EntityType] = 'PO' AND [EntityKey] = ?", (po,)):
        try:
            p = archive.abs_path(d["RelPath"])
            if os.path.isfile(p):
                out.add(sha256_file(p))
        except (OSError, ValueError):
            pass
    return out


def summary(decisions):
    c = Counter(d["status"] for d in decisions)
    lines = ["ملفات في الورقة: %d" % len(decisions),
             "  ستُنسخ:                      %d" % c.get("will_copy", 0),
             "  موجودة في النظام (تُتجاهل): %d" % c.get("already_there", 0),
             "  الأمر غير موجود في النظام:   %d" % c.get("order_missing", 0),
             "  الملف غير موجود على القرص:   %d" % c.get("file_missing", 0)]
    for k, v in (("order_ambiguous", "أمر ملتبس"), ("file_unreadable", "ملف لا يُقرأ"), ("rejected", "رفضه النظام (ليس PDF سليماً أو مسار طويل)"), ("copied", "نُسخ فعلاً")):
        if c.get(k):
            lines.append("  %s: %d" % (v, c[k]))
    by_type = Counter((d["type"], d["status"]) for d in decisions)
    lines.append("حسب النوع (ستُنسخ):  " + "  ".join("%s=%d" % (TYPE_NAME[t], by_type.get((t, "will_copy"), 0) + by_type.get((t, "copied"), 0)) for t in TYPE_MAP))
    missing_orders = sorted({d["po"] for d in decisions if d["status"] == "order_missing"})
    if missing_orders:
        lines.append("أوامر غير موجودة في النظام: " + "، ".join(missing_orders[:30]) + (" …" if len(missing_orders) > 30 else ""))
    return "\n".join(lines)


def apply(decisions, user="import"):
    import archive
    import db
    for d in decisions:
        if d["status"] != "will_copy":
            continue
        try:
            folder = archive.po_doc_folder_rel(archive.ensure_po_folder(d["po_real"]), TYPE_MAP[d["type"]])
            name = d["saved"] or os.path.basename(d["src"])
            if not name.lower().endswith(".pdf"):
                name += ".pdf"
            row = archive.store_document("PO", d["po_real"], folder, TYPE_MAP[d["type"]], d["src"], name,
                                         note="مستورد من النظام القديم (معرّف %s)" % d["id"], user=user, keep_source=True)
            if d.get("added") and isinstance(d["added"], datetime):
                try:
                    db.execute("UPDATE [Documents] SET [UploadedAt] = ? WHERE [DocID] = ?", (d["added"], row["DocID"]))
                except Exception:                                  # noqa: BLE001 - the date is a nicety, never a reason to fail
                    pass
            d["status"], d["note"] = "copied", row["RelPath"]
        except Exception as e:                                     # noqa: BLE001
            d["status"], d["note"] = "rejected", "%s: %s" % (type(e).__name__, e)
    return decisions


def write_report(decisions, path):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["الحالة", "النوع", "رقم الأمر (الورقة)", "رقم الأمر (النظام)", "الملف المصدر", "ملاحظة"])
        for d in decisions:
            w.writerow([d["status"], TYPE_NAME[d["type"]], d["po"], d["po_real"], d.get("src") or d["path"], d["note"]])


def main(argv=None):
    ap = argparse.ArgumentParser(description="Import the PDFs of the old PO tracker into the archive")
    ap.add_argument("xlsx")
    ap.add_argument("--map", action="append", help='old path prefix = new path prefix (can be repeated)')
    ap.add_argument("--search", action="append", help="a folder to look in when the old path is not found (searched by file name)")
    ap.add_argument("--report", help="CSV file for the full list (default: next to the xlsx)")
    ap.add_argument("--apply", action="store_true", help="really copy (without it: preview only)")
    ap.add_argument("--yes", action="store_true", help="confirms that a backup was taken and the server is stopped")
    args = ap.parse_args(argv)
    if not os.path.isfile(args.xlsx):
        print("الملف غير موجود:", args.xlsx)
        return 2
    rows = read_rows(args.xlsx)
    decisions = plan(rows, parse_maps(args.map), index_files(args.search), load_orders(), hashes_of_order)
    print(summary(decisions))
    if args.apply:
        if not args.yes:
            print("\nللنسخ الفعلي أضف --yes (بعد أخذ نسخة احتياطية وإيقاف الخادم).")
            write_report(decisions, args.report or os.path.splitext(args.xlsx)[0] + "_pdf_import_preview.csv")
            return 3
        apply(decisions)
        print("\nبعد النسخ:\n" + summary(decisions))
    rep = args.report or os.path.splitext(args.xlsx)[0] + ("_pdf_import_result.csv" if args.apply else "_pdf_import_preview.csv")
    write_report(decisions, rep)
    print("\nالتقرير الكامل:", rep)
    return 0


if __name__ == "__main__":
    sys.exit(main())

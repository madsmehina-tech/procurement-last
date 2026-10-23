# -*- coding: utf-8 -*-
r"""
python tools\make_po_pdfs.py            - writes the PDF + data copy of every purchase order that has none yet
python tools\make_po_pdfs.py --all      - rewrites them for ALL orders
python tools\make_po_pdfs.py PO-2026-001 PO-2026-002   - only these orders
Needs Microsoft Edge or Google Chrome on this PC (found automatically). Stop the web server first if you can.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import archive
import db
import pdfgen
import po_files


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not pdfgen.available():
        print("[!!] لم يُعثر على Microsoft Edge أو Google Chrome. حدّد مساره في config.ini تحت [pdf] browser_path")
        return 1
    from app import app
    have = {d["EntityKey"] for d in db.query("SELECT [EntityKey], [DocType], [IsCurrent] FROM [Documents] WHERE [EntityType] = ?", ("PO",))
            if d["DocType"] == archive.PO_PDF and d["IsCurrent"]}
    todo = args or [o["PONumber"] for o in db.query("SELECT [PONumber] FROM [PurchaseOrders]") if "--all" in sys.argv or o["PONumber"] not in have]
    print("عدد الأوامر المطلوبة:", len(todo))
    ok = bad = 0
    with app.test_request_context():                     # the print page is rendered by the web application itself
        for po in todo:
            res = po_files.save_current(po, "tools")
            if res["pdf"]:
                ok += 1
                print("  ✔", po)
            else:
                bad += 1
                print("  ✘", po, "-", res["error"])
    print("تم: %d | فشل: %d" % (ok, bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())

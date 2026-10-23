# -*- coding: utf-8 -*-
"""Tests, on THIS computer, the two things the contracts need: filling a Word template and turning the Word file into a PDF.
Run:  python tools\\test_contract_pdf.py        (it opens Word invisibly for a few seconds)"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import contract_pdf
import contract_render
import contract_vars as V


def main():
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "contract_starters", "عقد_مقاول_من_الباطن.docx")
    out = tempfile.mkdtemp(prefix="po_test_")
    docx, pdf = os.path.join(out, "test.docx"), os.path.join(out, "test.pdf")
    values = {k: "قيمة تجريبية" for k in V.STANDARD}
    tables = {"جدول_الأسعار": {"header": ["م", "البند", "السعر"], "rows": [["1", "بند تجريبي", "100"]]}, "جدول_المواصفات": {"header": ["م", "العمل", "الوصف"], "rows": [["1", "أ", "ب"]]},
              "جدول_الدفعات": {"header": ["الدفعة", "النسبة"], "rows": [["الأولى", "100%"]]}}
    contract_render.render(src, docx, values, tables)
    print("1) ملف Word اتعمل:", docx)
    t = time.time()
    try:
        used = contract_pdf.convert(docx, pdf)
        print("2) PDF اتعمل بواسطة %s في %.1f ثانية: %s (%d بايت)" % (used, time.time() - t, pdf, os.path.getsize(pdf)))
        print("\nافتح الملفين وتأكد أن العربية والجداول سليمة. النتيجة: ✔ جاهز")
        os.startfile(out) if sys.platform == "win32" else None
    except contract_pdf.PdfUnavailable as e:
        print("2) PDF لم يتعمل:", e)
        print("\nالنتيجة: ✘ Word ينتج بدون PDF (تقدر تفتحه وتحفظه PDF يدويًا)")


if __name__ == "__main__":
    main()

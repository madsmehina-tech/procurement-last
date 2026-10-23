# -*- coding: utf-8 -*-
r"""python tools\setup_check.py - a pre-flight check of the server PC before the team starts using it."""
import importlib
import os
import socket
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

bad = []


def line(ok, text, fatal=True):
    print(("[OK] " if ok else "[!!] ") + text)
    if not ok and fatal:
        bad.append(text)


def main():
    print("PO Web v%s - فحص الجاهزية\n" % config.APP_VERSION)
    line(sys.version_info >= (3, 9), "إصدار بايثون %d.%d (المطلوب 3.9 فأحدث)" % sys.version_info[:2])
    for mod, why, fatal in [("flask", "الأساسي", True), ("pyodbc", "قراءة قاعدة Access", True),
                            ("waitress", "الخادم الإنتاجي (pip install waitress)", False),
                            ("openpyxl", "تصدير Excel وقراءة عروض Excel", True),
                            ("pymupdf", "قراءة عروض PDF (pymupdf)", False),
                            ("pytesseract", "قراءة الصور OCR (اختيارية)", False),
                            ("docx", "وحدة العقود: توليد ملفات Word (pip install python-docx)", False),
                            ("hijridate", "التاريخ الهجري", False)]:
        try:
            importlib.import_module(mod)
            line(True, "المكتبة %s" % mod)
        except ImportError:
            line(False, "المكتبة %s غير مثبتة - %s" % (mod, why), fatal)
    try:
        import quote2po
        langs = quote2po.ocr_languages()
        if "ara" in langs:
            line(True, "قراءة الصور والملفات الممسوحة (OCR): جاهزة - اللغات: %s" % "+".join(langs))
        elif langs:
            line(False, "OCR يعمل بالإنجليزية فقط - ثبّت حزمة اللغة العربية (Arabic) مع Tesseract", fatal=False)
        else:
            line(False, "قراءة الصور (OCR) غير مفعّلة - اختيارية: راجع docs\\قراءة_الصور_OCR.md", fatal=False)
    except Exception:                                        # noqa - optional feature, never blocks
        pass
    try:
        import pdfgen
        browser = pdfgen.find_browser()
        line(bool(browser), ("إنشاء PDF لأمر الشراء: جاهز (%s)" % os.path.basename(browser)) if browser else
             "إنشاء PDF لأمر الشراء غير متاح: لم يُعثر على Edge أو Chrome - راجع docs\\ملفات_الأمر_PDF.md", fatal=False)
    except Exception:                                        # noqa - never blocks
        pass
    try:                                                     # DOCX -> PDF for the contracts: Word (best), then LibreOffice
        import contract_pdf
        word = False
        if sys.platform == "win32":
            try:
                import winreg
                import win32com.client                           # noqa
                winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "Word.Application"))
                word = True
            except Exception:                                    # noqa
                word = False
        if word:
            line(True, "تحويل عقود Word إلى PDF: جاهز (Microsoft Word)")
        elif contract_pdf._soffice():
            line(True, "تحويل عقود Word إلى PDF: جاهز (LibreOffice)")
        else:
            line(False, "تحويل عقود Word إلى PDF غير متاح (يلزم Word + pip install pywin32). العقد يُنشأ Word دائمًا؛ راجع docs\\قوالب_العقود.md", fatal=False)
    except Exception:                                            # noqa - never blocks
        pass
    line(os.path.isfile(config.CONFIG_FILE), "ملف الإعداد: %s" % config.CONFIG_FILE, fatal=False)
    if not os.path.isfile(config.CONFIG_FILE):
        print("      (انسخ config.ini.example إلى config.ini وعدّل المسارات)")
    line(os.path.isfile(config.DB_PATH), "ملف القاعدة موجود: %s" % config.DB_PATH)
    try:
        import db
        for t in ("PurchaseOrders", "Users", "Documents", "Certificates", "Roles", "AuditLog", "Payments", "Sites", "PaymentCycles", "CycleDecisions", "CycleLog", "LedgerEntries", "ContractTemplates", "CompanyProfile", "Comparisons", "QuoteLines"):
            line(db.table_exists(t), "الجدول %s" % t + ("" if db.table_exists(t) else "  ← نفّذ: python migrate.py"))
    except Exception as e:                                   # noqa
        line(False, "تعذّر فتح القاعدة: %s" % e)
    try:
        import db
        rows = db.query("SELECT [SupplierName], [Project] FROM [PurchaseOrders]")
        rows += db.query("SELECT [SiteName] AS SupplierName, [Project] FROM [Contracts]") if db.table_exists("Contracts") else []
        texts = [str(v) for r in rows for v in r.values() if v]
        arabic = [t for t in texts if any("\u0600" <= ch <= "\u06FF" for ch in t)]
        line(True, "قراءة النصوص العربية من القاعدة: %d نصًّا عربيًا من %d سجلًّا - ترميز هذا الجهاز: %s" % (len(arabic), len(rows), db.ansi_codec()))
        lost = [t for t in texts if "?" in t]
        if lost:
            line(False, "%d نصًّا فيه علامة ؟ (حرف لا يدعمه ترميز هذا الجهاز) مثل: %s - لا تعدّل هذه السجلات من هذا الجهاز" % (len(lost), lost[0][:40]), fatal=False)
        if db.ansi_codec() not in ("utf-8", "cp1256"):
            line(False, "لغة ويندوز على هذا الجهاز ليست عربية (%s): الكتابة العربية ستتحول إلى ؟ - غيّر Region ← Administrative ← System locale إلى Arabic أو فعّل UTF-8" % db.ansi_codec(), fatal=False)
    except UnicodeDecodeError:
        line(False, "فشلت قراءة النصوص العربية من القاعدة  ← نفّذ: python tools\\diagnose_text.py وأرسل لي النتيجة")
    except Exception:                                        # noqa - the table checks above already report other problems
        pass
    try:
        os.makedirs(config.ARCHIVE_ROOT, exist_ok=True)
        t = os.path.join(config.ARCHIVE_ROOT, ".write_test")
        open(t, "w").write("x")
        os.remove(t)
        line(True, "مجلد الأرشيف قابل للكتابة: %s" % config.ARCHIVE_ROOT)
    except OSError as e:
        line(False, "مجلد الأرشيف غير قابل للكتابة: %s" % e)
    if config.SHARE_PATH:
        line(os.path.isdir(config.SHARE_PATH), "المشاركة على الشبكة تفتح من هذا الجهاز: %s" % config.SHARE_PATH, fatal=False)
    else:
        line(False, "share_path غير مضبوط في config.ini (للعرض فقط - اختياري)", fatal=False)
    s = socket.socket()
    try:
        s.bind((config.HOST if config.HOST != "0.0.0.0" else "", config.PORT))
        line(True, "المنفذ %d متاح" % config.PORT)
    except OSError:
        line(False, "المنفذ %d مشغول (هل الخادم يعمل بالفعل؟)" % config.PORT, fatal=False)
    finally:
        s.close()
    try:
        ips = [ip for ip in socket.gethostbyname_ex(socket.gethostname())[2] if not ip.startswith("127.")]
        print("\nعناوين هذا الجهاز:", ", ".join(ips) or "-")
        print("ثبّت عنوان الجهاز (DHCP Reservation في الراوتر) حتى لا يتغير فتنقطع الأجهزة الأخرى.")
    except OSError:
        pass
    print("\n" + ("⚠ توجد %d مشكلة تمنع التشغيل." % len(bad) if bad else "✔ الجهاز جاهز."))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""
Runs every migration that has not been applied yet, in order, and records it in SchemaVersion.
All migrations are idempotent ("add it only if it is missing"), so running this on a database that
was set up by hand with the old one-off scripts is safe: it simply finds everything already there.

    python migrate.py           # apply what is pending
    python migrate.py --status  # show what is applied / pending
"""
import importlib
import os
import re

import db

DESCRIPTIONS = {
    "m000_base_tables": "الجداول الأساسية: الموردون وأوامر الشراء وأصنافها (تُنشأ فقط في قاعدة جديدة)",
    "m001_users": "جدول المستخدمين",
    "m002_supplier_files": "ملفات الموردين وحساب البنك",
    "m003_subcontractors": "جداول مقاولي الباطن والعقود والمستخلصات",
    "m004_subcontractor_files": "ملفات المقاول والخصومات",
    "m005_contract_sites_field": "حقل الموقع ونسبة الاستقطاع",
    "m006_cycles_and_certificates": "دورة الاعتماد والمواقع المتعددة ودورات الصرف",
    "m007_quote_file": "ملف عرض السعر لأمر الشراء",
    "m008_receivers": "جدول المستلمين",
    "m009_print_fields": "حقول الطباعة (خصم/ملاحظات/إخفاء)",
    "m010_location_signed": "الموقع والنسخة المعتمدة",
    "m011_archive_documents": "فهرس المستندات (Documents) ومجلد الأرشيف لكل أمر شراء ومستخلص",
    "m012_users_roles": "المستخدمون والأدوار والصلاحيات والمجموعات وسجل التدقيق",
    "m013_projects_revisions": "قائمة المشاريع الموحّدة وسجل نسخ أوامر الشراء وتواريخ انتهاء المستندات",
    "m014_supplier_documents": "نقل ملفات الموردين القديمة إلى هيكل «أوراق المورد»",
    "m015_order_files": "هيكل مجلد الأمر الجديد وأرشفة نسخ التعديلات",
    "m016_subcontracts": "العقود والمستخلصات والدفعات بالتصميم المعتمد ونقل أرشيف مقاولي الباطن",
    "m017_masterdata": "ربط الأوامر بالمورد والمشروع والموقع ومستوى الموقع في الأرشيف وأعمدة ناقصة",
    "m018_cycles_archive": "دورات الصرف وأرشيف معاملات الصرف وربط العقود بالمشروع والموقع",
    "m019_contract_folders": "مجلد لكل عقد ونموذج المستخلص الجديد وحالات أسطر الدورة",
    "m020_cycle_workflow": "دورة الصرف بثلاث مراحل: حالة التنفيذ لكل سطر وسجل كامل لكل دورة",
    "m021_certificates_independent": "المستخلص مستقل عن العقد: مقاول ومشروع وموقع خاصة به",
    "m022_ledger": "كشف حساب المقاول والدفعات المباشرة",
    "m023_payment_archive": "أرشفة الدفعات: رقم الدفعة وفولدرها وسند الصرف",
    "m024_quote_comparison": "مقارنة عروض الأسعار: المقارنات والبنود الموحدة والعروض وسطورها وأسماء البنود المتعلَّمة",
    "m025_contract_module": "وحدة العقود: عقود من قوالب Word للمقاولين والموردين",
}


def discover():
    here = os.path.dirname(os.path.abspath(__file__))
    return sorted(f[:-3] for f in os.listdir(here) if re.match(r"^m\d{3}_.*\.py$", f))


def ensure_version_table():
    if not db.table_exists("SchemaVersion"):
        db.execute("CREATE TABLE [SchemaVersion] ([Version] TEXT(60), [Description] TEXT(200), [AppliedAt] DATETIME)")


def applied():
    ensure_version_table()
    return {r["Version"] for r in db.query("SELECT [Version] FROM [SchemaVersion]")}


def refresh_descriptions():
    """Rewrites the (documentation-only) description column from the known list - repairs rows that were
    written with the wrong text encoding on a PC whose Windows code page is not UTF-8."""
    for version, text in DESCRIPTIONS.items():
        try:
            db.execute("UPDATE [SchemaVersion] SET [Description] = ? WHERE [Version] = ?", (text, version))
        except Exception:                          # noqa - purely cosmetic, never block a migration
            pass


def pending():
    """Names of migrations not applied yet (empty list = the database is up to date)."""
    if not db.table_exists("PurchaseOrders"):
        return []
    done = applied()
    return [n for n in discover() if n not in done]


def status():
    done = applied()
    for name in discover():
        print(("  [x] " if name in done else "  [ ] ") + name)


def run(backup=True):
    if not db.table_exists("PurchaseOrders"):
        print("!! جدول PurchaseOrders غير موجود في", db.DB_PATH)
        print("   تحقق من مسار القاعدة في config.ini (القسم [database]) - هذه القاعدة يجب أن تكون")
        print("   قاعدة PurchaseSystem.accdb التي يكتب إليها ملف الإكسل.")
        return False
    done = applied()
    refresh_descriptions()
    pending = [n for n in discover() if n not in done]
    if not pending:
        print("لا توجد ترحيلات معلّقة - القاعدة محدّثة.")
        return True
    if backup:
        import backup as backup_module
        try:
            copy = backup_module.backup_database("before_" + pending[0].split("_")[0])
        except OSError as e:
            print("!! تعذّر عمل النسخة الاحتياطية (%s) فلم يُنفَّذ أي ترحيل حفاظًا على بياناتك." % e)
            print("   تحقق من مسار backup_dir في config.ini، أو شغّل بدونها بوعي:  python migrate.py --no-backup")
            return False
        if copy:
            print("نسخة احتياطية قبل التحديث:", copy)
    for name in pending:
        mod = importlib.import_module("migrations." + name)
        desc = getattr(mod, "DESCRIPTION", None) or DESCRIPTIONS.get(name, "")
        print("== %s  %s" % (name, desc))
        mod.main()
        db.execute("INSERT INTO [SchemaVersion] ([Version], [Description], [AppliedAt]) VALUES (?, ?, Now())",
                   (name, desc))
    print("تم تطبيق %d ترحيل." % len(pending))
    return True

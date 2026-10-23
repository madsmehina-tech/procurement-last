# -*- coding: utf-8 -*-
"""
audit.py - the audit trail: who did what, when, from which PC.

Two sources:
1. Every successful form submission (any POST) is recorded automatically by app.py - so a page added later is
   covered without extra code.
2. Security events (sign-in, failed sign-in, registration, approvals, role changes ...) call log() explicitly.
Never raises: a problem writing the log must not stop the work itself.
"""
import logging

from flask import g, has_request_context, request, session

import db

_log = logging.getLogger("po.error")

# friendly Arabic names for the actions people will actually read in the log
LABELS = {
    "login_ok": "تسجيل دخول", "login_failed": "محاولة دخول فاشلة", "login_blocked": "دخول موقوف مؤقتًا",
    "login_pending": "محاولة دخول لحساب بانتظار الموافقة", "logout": "تسجيل خروج",
    "register": "طلب تسجيل مستخدم جديد", "user_approve": "الموافقة على مستخدم", "user_reject": "رفض طلب تسجيل",
    "user_update": "تعديل دور مستخدم", "user_toggle": "تفعيل/تعطيل مستخدم", "user_reset_password": "إعادة تعيين كلمة مرور",
    "roles_save": "تعديل صلاحيات الأدوار", "role_create": "إنشاء دور", "role_delete": "حذف دور",
    "project_add": "إضافة مشروع", "project_rename": "إعادة تسمية مشروع", "project_merge": "دمج مشروعين", "project_toggle": "تفعيل/إيقاف مشروع",
    "group_create": "إنشاء مجموعة", "group_save": "تعديل أعضاء مجموعة", "group_delete": "حذف مجموعة",
    "settings_profile_save": "تعديل بيانات الحساب", "settings_password_save": "تغيير كلمة المرور",
    "settings_avatar_save": "تغيير صورة المستخدم",
    "order_new": "إنشاء أمر شراء", "update_status": "تحديث حالة أمر شراء", "order_signed_upload": "رفع النسخة المعتمدة",
    "document_upload": "رفع مستند", "document_hide": "إزالة مستند من القائمة", "order_edit": "تعديل أمر شراء", "order_pdf": "إنشاء PDF لأمر شراء", "supplier_new": "إضافة مورد", "supplier_edit": "تعديل مورد",
    "subcontractor_new": "إضافة مقاول باطن", "subcontractor_edit": "تعديل مقاول باطن",
    "contract_new": "إنشاء عقد", "contract_site_add": "إضافة موقع لعقد", "contract_file_upload": "رفع ملف عقد",
    "certificate_new": "إدخال مستخلص", "certificate_new_any": "إدخال مستخلص", "certificate_reject": "رفض مستخلص", "certificate_reopen": "إعادة فتح مستخلص", "contract_edit": "تعديل عقد", "cycle_new": "إنشاء دورة صرف", "cycle_decisions": "حفظ / انتقال مرحلة في دورة صرف", "cycle_delete": "حذف دورة صرف", "ledger_add": "إضافة حركة في الدفعات (دائن / مدين)", "compare_new": "إنشاء مقارنة عروض أسعار", "contract_generate": "إنشاء عقد من قالب", "contract_generate_edit": "تعديل بيانات عقد مولَّد", "contract_files_generate": "إنشاء ملفات العقد (Word/PDF)", "contract_signed_upload": "رفع النسخة الموقّعة للعقد", "contract_template_new": "إضافة قالب عقد", "contract_template_version": "إضافة نسخة قالب عقد", "contract_template_update": "تعديل بيانات قالب عقد", "settings_company": "تعديل بيانات الشركة", "compare_quote_add": "إضافة عرض سعر لمقارنة", "compare_match_save": "مطابقة بنود مقارنة", "compare_award": "اعتماد العرض الفائز (ترسية)", "compare_reopen": "إلغاء ترسية", "compare_delete": "حذف مقارنة", "ledger_files": "رفع ملف لحركة مالية", "cycle_transfer_files": "رفع ملفات تحويل مقاول", "ledger_execute": "تنفيذ دفعة مباشرة", "ledger_delete": "حذف حركة من الدفعات",
    "cycle_reopen": "رجوع مرحلة / إعادة فتح دورة", "cycle_transfer": "تحديد حالة تنفيذ التحويل", "cycle_transfer_undo": "إلغاء حالة التنفيذ",
    "archive_close": "إقفال معاملة صرف", "archive_reopen": "إعادة فتح معاملة صرف", "site_rename": "إعادة تسمية موقع",
    "site_merge": "دمج مواقع", "certificate_edit": "تعديل مستخلص", "certificate_review": "مراجعة مستخلص",
    "certificate_approve": "اعتماد مستخلص", "certificate_pay": "تسجيل صرف مستخلص", "certificate_unpay": "إلغاء تسجيل صرف",
}
# form posts that are not worth an automatic line (they log themselves, or only read data)
AUTO_SKIP = {"login", "register", "order_parse_quote"}
ENTITY_KEYS = [("po_number", "أمر شراء"), ("cert_id", "مستخلص"), ("contract_id", "عقد"), ("sub_id", "مقاول باطن"),
               ("supplier_id", "مورد"), ("doc_id", "مستند"), ("user_id", "مستخدم")]


def log(action, entity_type="", entity_key="", details="", user=None):
    try:
        if user is None and has_request_context() and "user_id" in session:
            user = g.get("_po_user") or None
        uid = (user or {}).get("UserID")
        uname = (user or {}).get("Username", "")
        ip = request.remote_addr if has_request_context() else ""
        db.execute(
            "INSERT INTO [AuditLog] ([LoggedAt], [UserID], [Username], [ActionName], [EntityType], [EntityKey], "
            "[Details], [IPAddress]) VALUES (Now(), ?, ?, ?, ?, ?, ?, ?)",
            (uid, uname, action, entity_type or "", str(entity_key or ""), (details or "")[:250], ip or ""),
        )
    except Exception:                                    # noqa - never break the real work
        _log.exception("audit log failed")


def log_request(response):
    """Called from app.after_request: one line for every successful POST."""
    try:
        ep = request.endpoint
        if request.method != "POST" or not ep or ep in AUTO_SKIP or response.status_code >= 400:
            return
        if "user_id" not in session:
            return
        etype, ekey = "", ""
        for key, label in ENTITY_KEYS:
            if key in (request.view_args or {}):
                etype, ekey = label, request.view_args[key]
                break
        if not etype and request.form.get("entity_key"):
            etype, ekey = request.form.get("entity_type", ""), request.form.get("entity_key")
        log(ep, etype, ekey, request.form.get("doc_type", ""))
    except Exception:                                    # noqa
        _log.exception("audit request log failed")


def label(action):
    return LABELS.get(action, action)

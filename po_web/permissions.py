# -*- coding: utf-8 -*-
"""
permissions.py - the single list of what a user can be allowed to do, the default roles, and which
permission protects each page.

How protection works (and why a page can never be forgotten):
- Every page (endpoint) MUST appear in ENDPOINT_PERMS. When the server starts, routes.register_all() checks
  that every registered page has an entry here and refuses to start if one is missing.
- The entry says what the page needs:  "orders.view" (a permission) | None (any signed-in user) | PUBLIC (no
  sign-in at all) | a function that decides from the request (documents).
- The Administrator role always passes. Other roles pass only with the permission ticked in their role.
"""
from flask import request

PUBLIC = "__public__"
ADMIN_KEY = "admin"

# (group label, [(code, label), ...]) - the order here is the order of the permission matrix screen
CATALOG = [
    ("المشتريات", [
        ("orders.view", "عرض أوامر الشراء ومتابعتها"),
        ("orders.create", "إنشاء أمر شراء"),
        ("orders.edit", "تحديث حالات الأمر ورفع مستنداته"),
        ("orders.print", "طباعة أمر الشراء"),
    ]),
    ("مقارنة عروض الأسعار", [
        ("compare.view", "عرض مقارنات عروض الأسعار وتقاريرها"),
        ("compare.edit", "إنشاء المقارنات ورفع العروض ومراجعتها ومطابقة البنود"),
        ("compare.award", "اعتماد العرض الفائز (الترسية)"),
    ]),
    ("عقود من قوالب Word", [
        ("contractgen.create", "إنشاء العقود والاتفاقيات من القوالب وإنشاء ملفاتها ورفع النسخة الموقّعة"),
        ("contractgen.templates", "إدارة قوالب العقود وبيانات الشركة (الطرف الأول)"),
    ]),
    ("الموردون", [
        ("suppliers.view", "عرض الموردين وملفاتهم"),
        ("suppliers.edit", "إضافة وتعديل الموردين"),
    ]),
    ("الأصناف", [("items.view", "عرض الأصناف")]),
    ("لوحة المؤشرات", [
        ("analytics.view", "عرض لوحة المؤشرات"),
        ("analytics.export", "تصدير وطباعة اللوحة"),
    ]),
    ("مقاولو الباطن", [
        ("subcontractors.view", "عرض مقاولي الباطن وتقاريرهم"),
        ("subcontractors.edit", "إضافة وتعديل مقاولي الباطن"),
    ]),
    ("العقود", [
        ("contracts.view", "عرض العقود"),
        ("contracts.edit", "إنشاء وتعديل العقود ومواقعها"),
        ("contracts.delete", "حذف عقد لا مستخلصات له"),
    ]),
    ("المستخلصات", [
        ("certificates.view", "عرض المستخلصات"),
        ("certificates.edit", "إدخال وتعديل المستخلصات"),
        ("certificates.pay", "تسجيل صرف المستخلص"),
        ("certificates.delete", "حذف مستخلص (غير المصروف)"),
    ]),
    ("الدفعات", [("payments.view", "عرض الدفعات وكشف حساب المقاول"), ("payments.edit", "إضافة حركات الدفعات (دائن / مدين) وتنفيذها وحذفها")]),
    ("دورة الصرف", [
        ("cycles.view", "عرض دورات الصرف"),
        ("cycles.manage", "إنشاء الدورات وتسجيل قرارات الإدارة"),
    ]),
    ("الأرشيف", [("archive.view", "تصفح أرشيف معاملات الصرف"), ("archive.close", "إقفال معاملة الصرف"), ("archive.reopen", "إعادة فتح معاملة مُقفلة (صلاحية أعلى)")]),
    ("الإعدادات", [
        ("settings.users", "إدارة المستخدمين والموافقة على طلبات التسجيل"),
        ("settings.roles", "إدارة الأدوار والصلاحيات والمجموعات"),
        ("settings.audit", "عرض سجل التدقيق"),
        ("settings.projects", "إدارة قائمة المشاريع (إعادة تسمية ودمج)"),
        ("settings.system", "إعدادات النظام"),
    ]),
]

ALL_CODES = [code for _, items in CATALOG for code, _ in items]
LABELS = {code: label for _, items in CATALOG for code, label in items}


def _views():
    return [c for c in ALL_CODES if c.endswith(".view")]


# key, Arabic name, description, permissions (the Administrator gets everything, so it has no list)
DEFAULT_ROLES = [
    (ADMIN_KEY, "مدير النظام (Administrator)", "كل الصلاحيات، ولا يمكن تعديله", None),
    ("procurement", "مشتريات", "أوامر الشراء والموردون والأصناف ولوحة المؤشرات",
     ["orders.view", "orders.create", "orders.edit", "orders.print", "suppliers.view", "suppliers.edit",
      "items.view", "analytics.view", "analytics.export"]),
    ("contracts", "عقود", "مقاولو الباطن والعقود والمستخلصات",
     ["subcontractors.view", "subcontractors.edit", "contracts.view", "contracts.edit", "certificates.view",
      "certificates.edit", "payments.view", "cycles.view", "archive.view", "analytics.view"]),
    ("finance", "مالية", "صرف المستخلصات والدفعات ودورات الصرف",
     ["orders.view", "certificates.view", "certificates.pay", "payments.view", "cycles.view", "cycles.manage",
      "archive.view", "archive.close", "analytics.view", "analytics.export", "subcontractors.view", "contracts.view"]),
    ("viewer", "عرض فقط", "الاطلاع على كل الشاشات دون أي تعديل", _views()),
]


def _doc_upload_perm(**kw):
    return {"CERT": "certificates.edit", "SUPPLIER": "suppliers.edit", "SUB": "subcontractors.edit", "CYCLE": "cycles.manage", "CONTRACT": "contracts.edit", "LEDGER": "payments.edit", "PAYMENT": "cycles.manage", "QUOTE": "compare.edit", "COMPARE": "compare.edit"}.get(request.form.get("entity_type"), "orders.edit")


def _doc_view_perm(**kw):
    import archive
    doc = archive.get_document(kw.get("doc_id"))
    return {"CERT": "certificates.view", "SUPPLIER": "suppliers.view", "SUB": "subcontractors.view", "CYCLE": "cycles.view", "CONTRACT": "contracts.view", "LEDGER": "payments.view", "PAYMENT": "payments.view", "QUOTE": "compare.view", "COMPARE": "compare.view"}.get(doc.get("EntityType") if doc else None, "orders.view")


def _doc_hide_perm(**kw):
    import archive
    doc = archive.get_document(kw.get("doc_id"))
    return {"CERT": "certificates.edit", "SUPPLIER": "suppliers.edit", "SUB": "subcontractors.edit", "CYCLE": "cycles.manage", "CONTRACT": "contracts.edit", "LEDGER": "payments.edit", "PAYMENT": "cycles.manage", "QUOTE": "compare.edit", "COMPARE": "compare.edit"}.get(doc.get("EntityType") if doc else None, "orders.edit")


# endpoint name -> what it needs
ENDPOINT_PERMS = {
    # public
    "login": PUBLIC, "logout": PUBLIC, "register": PUBLIC, "health": PUBLIC,
    # any signed-in user
    "dashboard": None, "global_search": None, "settings_account": None, "settings_profile_save": None,
    "settings_password_save": None, "settings_avatar_save": None, "avatar_view": None,
    # purchasing
    "orders_list": "orders.view", "order_detail": "orders.view", "order_signed_file": "orders.view",
    "order_quote_file": "orders.view", "update_status": "orders.edit", "order_signed_upload": "orders.edit",
    "order_new": "orders.create", "order_parse_quote": "orders.create", "order_quote_copy": "orders.create",
    "order_print": "orders.print",
    "items_list": "items.view", "item_detail": "items.view", "items_print": "items.view", "items_export_pdf": "items.view", "items_export_xlsx": "items.view",
    "order_preview": "orders.create", "order_edit": "orders.edit", "order_pdf": "orders.edit", "order_revision_print": "orders.print",
    "orders_analytics": "analytics.view", "orders_analytics_export": "analytics.export",
    "suppliers_list": "suppliers.view", "supplier_detail": "suppliers.view", "suppliers_export_xlsx": "suppliers.view", "suppliers_export_pdf": "suppliers.view",
    "supplier_new": "suppliers.edit", "supplier_edit": "suppliers.edit",
    # subcontractors, contracts, certificates
    "subcontractors_list": "subcontractors.view", "subcontractor_detail": "subcontractors.view", "subcontractors_export_xlsx": "subcontractors.view",
    "subcontractors_export_pdf": "subcontractors.view", "subcontractors_print": "subcontractors.view",
    "subcontractor_report": "subcontractors.view",
    "subcontractor_new": "subcontractors.edit", "subcontractor_edit": "subcontractors.edit",
    "contracts_list": "contracts.view", "contract_detail": "contracts.view", "contract_file_view": "contracts.view", "contract_edit": "contracts.edit",
    "contract_new": "contracts.edit", "contract_site_add": "contracts.edit", "contract_file_upload": "contracts.edit",
    "certificate_detail": "certificates.view", "certificate_file": "certificates.view",
    "certificates_list": "certificates.view", "certificates_export": "certificates.view", "certificate_new": "certificates.edit", "certificate_new_any": "certificates.edit",
    "certificate_add": "certificates.edit", "certificate_delete": "certificates.delete", "contract_delete": "contracts.delete",
    "certificate_edit": "certificates.edit", "certificate_reject": "certificates.edit", "certificate_reopen": "certificates.edit",
    "payments_list": "payments.view", "payments_export": "payments.view",
    "subs_dashboard": "analytics.view", "subs_dashboard_print": "analytics.export", "subs_dashboard_pdf": "analytics.export", "subs_dashboard_xlsx": "analytics.export",
    "project_analysis": "analytics.view", "project_analysis_print": "analytics.export", "project_analysis_pdf": "analytics.export", "project_analysis_xlsx": "analytics.export",
    "contractor_status_report": "subcontractors.view", "contractor_status_pdf": "subcontractors.view",
    "compare_list": "compare.view", "compare_view": "compare.view", "compare_table": "compare.view", "compare_match": "compare.view", "compare_quote": "compare.view", "compare_print": "compare.view",
    "compare_pdf": "compare.view", "compare_xlsx": "compare.view", "compare_template": "compare.view", "compare_new": "compare.edit", "compare_quote_add": "compare.edit", "compare_quote_save": "compare.edit",
    "compare_quote_delete": "compare.edit", "compare_match_save": "compare.edit", "compare_report_archive": "compare.edit", "compare_delete": "compare.edit", "compare_award": "compare.award",
    "compare_reopen": "compare.award", "compare_next_ref": "compare.award", "compare_to_order": "orders.create", "compare_to_contract": "contracts.edit",
    "contract_generate": "contractgen.create", "contract_generate_edit": "contractgen.create", "contract_files_generate": "contractgen.create", "contract_signed_upload": "contractgen.create",
    "contract_templates": "contractgen.templates", "contract_templates_starters": "contractgen.templates", "contract_templates_guide": "contractgen.create", "contract_template_new": "contractgen.templates",
    "contract_template_detail": "contractgen.templates", "contract_template_update": "contractgen.templates", "contract_template_version": "contractgen.templates", "contract_template_download": "contractgen.templates",
    "contract_template_test": "contractgen.templates", "settings_company": "contractgen.templates",
    "statement_view": "payments.view", "statement_print": "payments.view", "statement_pdf": "payments.view", "statement_xlsx": "payments.view",
    "ledger_add": "payments.edit", "ledger_files": "payments.edit", "payments_archive": "archive.view", "ledger_execute": "payments.edit", "ledger_delete": "payments.edit",
    # payment cycle (three stages)
    "cycles_list": "cycles.view", "cycles_history": "cycles.view", "cycle_detail": "cycles.view", "cycle_export": "cycles.view", "cycle_print": "cycles.view",
    "cycle_xlsx": "cycles.view", "cycle_pdf_download": "cycles.view", "cycle_order": "cycles.view", "cycle_orders_print": "cycles.view",
    "cycle_new": "cycles.manage", "cycle_refresh": "cycles.manage", "cycle_decisions": "cycles.manage", "cycle_reopen": "cycles.manage", "cycle_delete": "cycles.manage",
    "cycle_pdf": "cycles.manage", "cycle_signed": "cycles.manage", "cycle_order_pdf": "cycles.manage",
    "cycle_transfer": "certificates.pay", "cycle_transfer_undo": "certificates.pay", "cycle_transfer_files": "certificates.pay",
    # archive of payment transactions
    "archive_list": "archive.view", "archive_detail": "archive.view", "archive_export": "archive.view", "archive_close": "archive.close", "archive_reopen": "archive.reopen",
    # sites
    "settings_sites": "settings.projects", "site_rename": "settings.projects", "site_merge": "settings.projects", "site_toggle": "settings.projects",
    "certificate_pay": "certificates.pay", "certificate_unpay": "certificates.pay",
    # documents (depends on the owner)
    "document_upload": _doc_upload_perm, "document_view": _doc_view_perm, "document_hide": _doc_hide_perm,
    # settings
    "settings_users": "settings.users", "user_approve": "settings.users", "user_reject": "settings.users",
    "user_update": "settings.users", "user_toggle": "settings.users", "user_reset_password": "settings.users",
    "settings_roles": "settings.roles", "roles_save": "settings.roles", "role_create": "settings.roles",
    "role_delete": "settings.roles", "settings_groups": "settings.roles", "group_create": "settings.roles",
    "group_save": "settings.roles", "group_delete": "settings.roles",
    "settings_audit": "settings.audit", "settings_archive": "settings.system",
    "settings_projects": "settings.projects", "project_add": "settings.projects", "project_rename": "settings.projects",
    "project_merge": "settings.projects", "project_toggle": "settings.projects",
}

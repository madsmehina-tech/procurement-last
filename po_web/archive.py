# -*- coding: utf-8 -*-
"""
archive.py - the ONE place that decides where a file lives on disk and records it.

Folder structure (created automatically on the first operation for an order / certificate):

  <ARCHIVE_ROOT>/الموردين/<المورد>/<المشروع>/<رقم أمر الشراء>/
  <ARCHIVE_ROOT>/المقاولين/<مقاول الباطن>/<المشروع>/<الموقع>/مستخلص <الرقم>/

Rules that keep it safe and stable:
- The folder of an order / certificate is decided ONCE (when it is created) and stored in the
  database (ArchiveFolder). Renaming a supplier later never orphans the files.
- Names are cleaned for Windows but keep Arabic letters (unlike werkzeug.secure_filename, which
  strips them).
- Nothing is ever overwritten or deleted: replacing a document stores a new file and marks the old
  one "not current" - the previous versions stay on disk and in the Documents table.
- Every path that comes from the outside is checked to stay inside the archive root.
"""
import hashlib
import os
import re
import shutil
import threading
import time
from datetime import datetime

from flask import send_file

import config
import db

ALLOWED_EXT = {"pdf", "jpg", "jpeg", "png", "doc", "docx", "xls", "xlsx"}

ROOT_SUPPLIERS = "الموردين"
ROOT_SUBS = "إدارة مقاولي الباطن"
OLD_ROOT_SUBS = "المقاولين"                     # the root used before version 14 (m016 moves everything out of it)
PENDING = "_مؤقت"

PO_DOC_TYPES = ["أمر الشراء", "عرض السعر", "سند الاستلام", "الفاتورة", "سند السداد", "مرفق إضافي"]   # uploaded by people
# What the SYSTEM writes for every order (so the order itself is on disk, not only in the database):
PO_PDF, PO_DATA = "أمر الشراء PDF", "بيانات الأمر"
PO_OLD_PDF, PO_OLD_DATA = "نسخة سابقة PDF", "بيانات نسخة سابقة"
# الموردين / <المورد> / <المشروع> / <رقم الأمر> / { أمر الشراء | المرفقات | النسخ السابقة }
PO_SUBFOLDERS = ("أمر الشراء", "المرفقات", "النسخ السابقة")
PO_SUBFOLDER = {"أمر الشراء": "أمر الشراء", PO_PDF: "أمر الشراء", PO_DATA: "أمر الشراء",
                PO_OLD_PDF: "النسخ السابقة", PO_OLD_DATA: "النسخ السابقة"}        # every other type -> المرفقات
CERT_DOC_TYPES = ["المستخلص المعتمد", "ملف المستخلص من الموقع", "نموذج طلب الصرف", "الاتفاقية", "الفاتورة الضريبية", "سند الصرف", "خطاب", "مرفق ثبوتي"]
# The five papers of a closed payment transaction (in the order the company files them):
#   1 نموذج طلب الصرف (finance)   2 المستخلص   3 الاتفاقية (contract / quotation / agreement / purchase order)
#   4 الفاتورة الضريبية (requested just before the transfer)   5 سند التحويل البنكي
TRANSACTION_PAPERS = [("نموذج طلب الصرف", "نموذج طلب الصرف من المالية"), ("المستخلص المعتمد", "المستخلص"),
                      ("الاتفاقية", "الاتفاقية (عقد / عرض سعر / اتفاقية / أمر شراء)"), ("الفاتورة الضريبية", "الفاتورة الضريبية"),
                      ("سند الصرف", "سند التحويل البنكي")]
# إدارة مقاولي الباطن / <المقاول> / المستخلصات / <المشروع> / <الموقع> / المستخلص رقم N / { ملف المستخلص | الاعتمادات | الخطابات | المرفقات }
CERT_SUBFOLDERS = ("ملف المستخلص", "الاعتمادات", "الخطابات", "المرفقات")
CERT_SUBFOLDER = {"المستخلص المعتمد": "ملف المستخلص", "ملف المستخلص من الموقع": "ملف المستخلص", "نموذج طلب الصرف": "الاعتمادات", "سند الصرف": "الاعتمادات",
                  "خطاب": "الخطابات", "الاتفاقية": "المرفقات", "الفاتورة الضريبية": "المرفقات", "مرفق ثبوتي": "المرفقات"}
# Payment cycles: إدارة مقاولي الباطن / دورات الصرف / <السنة> / دورة رقم N / { الكشوف | أوامر الصرف | الكشف الموقّع }
CYCLES_ROOT = "دورات الصرف"
CYCLE_DOC_TYPES = ["كشف الدورة (للإدارة)", "الكشف النهائي", "الكشف الموقّع", "أمر الصرف", "مرفق إضافي"]
CYCLE_SUBFOLDERS = ("الكشوف", "أوامر الصرف", "الكشف الموقّع")
CYCLE_SUBFOLDER = {"كشف الدورة (للإدارة)": "الكشوف", "الكشف النهائي": "الكشوف", "الكشف الموقّع": "الكشف الموقّع", "أمر الصرف": "أوامر الصرف",
                   "مرفق إضافي": "الكشوف"}
# إدارة مقاولي الباطن / <المقاول> / أوراق المقاول / { السجل التجاري | الشهادات | العقود | ملفات أخرى }
SUB_PAPERS = "أوراق المقاول"
SUB_PAPER_FOLDERS = ("السجل التجاري", "الشهادات", "ملفات أخرى")
SUB_SLOTS = ["السجل التجاري", "الشهادة الضريبية", "شهادة الآيبان", "ملف تأهيلي"]
SUB_OTHER_TYPES = ["شهادات", "ملفات إضافية"]
SUB_DOC_TYPES = SUB_SLOTS + SUB_OTHER_TYPES
SUB_SUBFOLDER = {"السجل التجاري": "السجل التجاري", "الشهادة الضريبية": "الشهادات", "شهادة الآيبان": "الشهادات", "ملف تأهيلي": "الشهادات",
                 "شهادات": "الشهادات", "ملفات إضافية": "ملفات أخرى"}
# إدارة مقاولي الباطن / <المقاول> / العقود / عقد رقم XXX / ملفات العقد      (every contract has its own folder)
CONTRACTS_DIR, CONTRACT_FILES = "العقود", "ملفات العقد"
CONTRACT_DOC_TYPES = ["العقد الموقّع", "عقد DOCX", "عقد PDF", "ملحق", "مرفق إضافي"]
TRASH_ROOT = "_المحذوفات"                                  # deleted records keep their files here (never shown, never lost)
# Supplier papers: three single slots + «ملفات أخرى» (several files, each with a type). The folder follows the type:
#   الموردين / <المورد> / أوراق المورد / { ملف تأهيلي | كتالوج | شهادات | ملفات أخرى }
SUPPLIER_SLOTS = ["السجل التجاري", "الشهادة الضريبية", "الكتالوج"]
SUPPLIER_OTHER_TYPES = ["ملف تأهيلي", "كتالوجات", "شهادات", "مراجع المشاريع", "ملفات إضافية"]
SUPPLIER_DOC_TYPES = SUPPLIER_SLOTS + SUPPLIER_OTHER_TYPES
SUPPLIER_PAPERS = "أوراق المورد"
SUPPLIER_SUBFOLDER = {
    "السجل التجاري": "شهادات", "الشهادة الضريبية": "شهادات", "الكتالوج": "كتالوج", "ملف تأهيلي": "ملف تأهيلي",
    "كتالوجات": "كتالوج", "شهادات": "شهادات", "مراجع المشاريع": "ملفات أخرى", "ملفات إضافية": "ملفات أخرى",
}
MULTI_TYPES = {"عرض سعر أصلي", "مرفق إضافي", "مرفق ثبوتي", "خطاب", "أمر الصرف", "ملحق", "سند الصرف", "معاملة التحويل", "سند / إيصال", "فاتورة", PO_OLD_PDF, PO_OLD_DATA} | set(SUPPLIER_OTHER_TYPES) | set(SUB_OTHER_TYPES)   # these accumulate; every other type keeps ONE current file

# The older single-file columns are still filled, so existing links/screens keep working.
LEGACY_COLUMN = {
    ("PO", "أمر الشراء"): ("PurchaseOrders", "SignedFile", "PONumber", False),
    ("PO", "عرض السعر"): ("PurchaseOrders", "QuoteFile", "PONumber", False),
    ("CERT", "المستخلص المعتمد"): ("Certificates", "CertificateFile", "CertificateID", True),
    ("CERT", "سند الصرف"): ("Certificates", "PaymentProofFile", "CertificateID", True),
    ("SUB", "السجل التجاري"): ("Subcontractors", "CRFile", "SubcontractorID", True),
    ("SUB", "الشهادة الضريبية"): ("Subcontractors", "TaxCertFile", "SubcontractorID", True),
    ("SUB", "شهادة الآيبان"): ("Subcontractors", "BankCertFile", "SubcontractorID", True),
    ("SUB", "ملف تأهيلي"): ("Subcontractors", "QualificationFile", "SubcontractorID", True),
    ("CONTRACT", "العقد الموقّع"): ("Contracts", "ContractFile", "ContractID", True),
}

_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {"COM%d" % i for i in range(1, 10)} | {"LPT%d" % i for i in range(1, 10)}


class UnsupportedFile(ValueError):
    pass


# --------------------------------------------------------------------------- names & paths
NAME_CODEC = "cp1256"      # file and folder names follow ONE rule on every PC


def _storable(text):
    """A name every PC of the system can store unchanged (Arabic-Indic digits -> 0-9, unsupported symbols removed).
    A file name on disk must be EXACTLY what its record says - otherwise the file exists but is never found again.
    The rule is the strictest code page in use (the Arabic one of the office PCs), NOT the code page of the PC that
    happens to run: a name written at home (UTF-8) must still match its record when the office PC reads or moves it."""
    from textfit import fit_text
    return fit_text(text, NAME_CODEC)


def storable_filename(name):
    """A file name exactly as the database of this PC can store it (no length cut) - used when files are moved."""
    t = _BAD.sub(" ", _storable(name or ""))
    t = re.sub(r"\s+", " ", t).strip(" .")
    return t or "ملف"


def safe_name(text, fallback="غير محدد", maxlen=45):
    t = _BAD.sub(" ", _storable(str(text if text is not None else "")))   # '?' (unstorable) is not allowed in Windows names either
    t = re.sub(r"\s+", " ", t).strip(" .")
    if not t:
        t = fallback
    if t.split(".")[0].upper() in _RESERVED:
        t = "_" + t
    if len(t) > maxlen:
        # two long names that differ only after the cut would share ONE folder: keep them apart with a fingerprint
        tag = "~" + hashlib.md5(t.encode("utf-8")).hexdigest()[:4]
        return (t[:max(1, maxlen - len(tag))].rstrip(" .") + tag) if maxlen > len(tag) + 3 else t[:maxlen].rstrip(" .") or fallback
    return t.rstrip(" .") or fallback


def abs_path(rel):
    """Relative ('a/b/c') -> absolute path, refusing anything that would leave the archive root."""
    rel = (rel or "").replace("\\", "/").strip("/")
    root = os.path.normpath(config.ARCHIVE_ROOT)
    p = os.path.normpath(os.path.join(root, *[x for x in rel.split("/") if x]))
    if p != root and not p.startswith(root + os.sep):
        raise ValueError("path escapes the archive root")
    return p


def network_path(rel):
    """The folder as the other PCs see it (empty if no share_path is configured) - for display only."""
    if not config.SHARE_PATH or not rel:
        return ""
    return config.SHARE_PATH + "\\" + rel.replace("/", "\\")


def ensure_folder(rel):
    os.makedirs(abs_path(rel), exist_ok=True)
    return rel


def po_folder_rel(supplier, project, po_number, site=None):
    """الموردين / <المورد> / <المشروع> / <الموقع> / <رقم الأمر>   (an order without a site goes under «بدون موقع»)."""
    return "/".join([ROOT_SUPPLIERS, safe_name(supplier), safe_name(project), safe_name(site or "بدون موقع", maxlen=40),
                     safe_name(po_number, maxlen=30)])


def po_doc_folder_rel(po_folder, doc_type):
    """The sub-folder of an order that a document type belongs in."""
    return "/".join([po_folder, PO_SUBFOLDER.get(doc_type, "المرفقات")])


def cert_folder_rel(sub_name, project, site, cert_no, suffix=""):
    label = safe_name("المستخلص رقم %s%s" % (cert_no, suffix), maxlen=40)
    return "/".join([ROOT_SUBS, safe_name(sub_name), "المستخلصات", safe_name(project), safe_name(site, maxlen=35), label])


def cert_doc_folder_rel(cert_folder, doc_type):
    return "/".join([cert_folder, CERT_SUBFOLDER.get(doc_type, "المرفقات")])


def supplier_root_rel(supplier):
    return "/".join([ROOT_SUPPLIERS, safe_name(supplier)])


def supplier_doc_folder_rel(root_rel, doc_type):
    return "/".join([root_rel, SUPPLIER_PAPERS, SUPPLIER_SUBFOLDER.get(doc_type, "ملفات أخرى")])


def sub_root_rel(sub_name):
    return "/".join([ROOT_SUBS, safe_name(sub_name)])


def sub_doc_folder_rel(sub_root, doc_type):
    return "/".join([sub_root, SUB_PAPERS, SUB_SUBFOLDER.get(doc_type, "ملفات أخرى")])


def subcontractor_profile_rel(sub_name):
    """Kept for the old single-file form fields: the commercial register etc. now live in أوراق المقاول."""
    return "/".join([sub_root_rel(sub_name), SUB_PAPERS, "ملفات أخرى"])


def contract_label(contract_no, contract_id):
    return (contract_no or "").strip() or ("#%s" % contract_id)


def contract_folder_rel(sub_name, contract_no, contract_id):
    return "/".join([sub_root_rel(sub_name), CONTRACTS_DIR, safe_name("عقد رقم %s" % contract_label(contract_no, contract_id), maxlen=50)])


def ensure_contract_folder(contract_id):
    """The folder of a contract's files - decided once (Contracts.ArchiveFolder):
         contractor:  إدارة مقاولي الباطن / <المقاول> / العقود / عقد رقم XXX / ملفات العقد
         supplier:    الموردين / <المورد> / عقود واتفاقيات / عقد رقم XXX / ملفات العقد"""
    try:
        rows = db.query("SELECT [ContractNo], [SubcontractorID], [SupplierID], [PartyKind], [ArchiveFolder] FROM [Contracts] WHERE [ContractID] = ?", (contract_id,))
    except Exception:                                    # noqa - a database older than migration m025 has no SupplierID / PartyKind (restoring an old backup runs m019 first)
        rows = db.query("SELECT [ContractNo], [SubcontractorID], [ArchiveFolder] FROM [Contracts] WHERE [ContractID] = ?", (contract_id,))
    if not rows:
        return None
    c = rows[0]
    rel = c.get("ArchiveFolder")
    if not rel:
        if c.get("PartyKind") == "مورد" and c.get("SupplierID"):
            ensure_supplier_folder(c["SupplierID"])
            root = db.query("SELECT [ArchiveFolder] FROM [Suppliers] WHERE [SupplierID] = ?", (c["SupplierID"],))[0]["ArchiveFolder"]
            rel = "/".join([root, "عقود واتفاقيات", safe_name("عقد رقم %s" % contract_label(c.get("ContractNo"), contract_id), maxlen=50)])
        else:
            sub = db.query("SELECT [Name] FROM [Subcontractors] WHERE [SubcontractorID] = ?", (c["SubcontractorID"],))
            rel = contract_folder_rel(sub[0]["Name"] if sub else "", c.get("ContractNo"), contract_id)
        db.execute("UPDATE [Contracts] SET [ArchiveFolder] = ? WHERE [ContractID] = ?", (rel, contract_id))
    return ensure_folder(rel + "/" + CONTRACT_FILES)


def trash_folder(rel):
    """Moves a whole folder to the trash area (a deleted record keeps its files; the archive audit ignores this area)."""
    try:
        src = abs_path(rel)
    except ValueError:
        return None
    if not os.path.isdir(src):
        return None
    dest_rel = "/".join([TRASH_ROOT, datetime.now().strftime("%Y%m%d_%H%M%S")] + [p for p in rel.replace("\\", "/").split("/") if p])
    dest = abs_path(dest_rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.move(src, dest)
    return dest_rel


def forget_documents(entity_type, key):
    """The record is gone: its document rows go with it (the files were moved to the trash area)."""
    db.execute("DELETE FROM [Documents] WHERE [EntityType] = ? AND [EntityKey] = ?", (entity_type, str(key)))


# --------------------------------------------------------------------------- entity folders
def ensure_po_folder(po_number):
    """Folder of a purchase order. Decided once, stored in PurchaseOrders.ArchiveFolder, created now."""
    rows = db.query(
        "SELECT [SupplierName], [Project], [Location], [ArchiveFolder] FROM [PurchaseOrders] WHERE [PONumber] = ?",
        (po_number,),
    )
    if not rows:
        return None
    r = rows[0]
    rel = r.get("ArchiveFolder")
    if not rel:
        rel = po_folder_rel(r.get("SupplierName"), r.get("Project"), po_number, r.get("Location"))
        db.execute("UPDATE [PurchaseOrders] SET [ArchiveFolder] = ? WHERE [PONumber] = ?", (rel, po_number))
    for sub in PO_SUBFOLDERS:                                  # the three sub-folders always exist
        ensure_folder(rel + "/" + sub)
    return ensure_folder(rel)


def move_po_folder(po_number, new_rel):
    """Moves EVERYTHING of an order (every document, every version, its system files) to another folder - used when its
    supplier / project / site changes, and by the migration that added the site level. Each file: copy -> check ->
    update its record -> remove the old copy. Returns (moved, missing)."""
    rows = db.query("SELECT [ArchiveFolder] FROM [PurchaseOrders] WHERE [PONumber] = ?", (po_number,))
    if not rows:
        return 0, 0
    old = (rows[0].get("ArchiveFolder") or "").replace("\\", "/").rstrip("/")
    if not old or old == new_rel:
        db.execute("UPDATE [PurchaseOrders] SET [ArchiveFolder] = ? WHERE [PONumber] = ?", (new_rel, po_number))
        ensure_po_folder(po_number)
        return 0, 0
    moved = missing = 0
    for d in db.query("SELECT [DocID], [RelPath] FROM [Documents] WHERE [EntityType] = ? AND [EntityKey] = ?", ("PO", po_number)):
        rel = (d["RelPath"] or "").replace("\\", "/")
        if not rel.startswith(old + "/"):
            continue
        sub = rel[len(old) + 1:].rsplit("/", 1)
        target = new_rel + ("/" + sub[0] if len(sub) == 2 else "")
        if relocate_document(d["DocID"], target):
            moved += 1
        else:
            missing += 1
    db.execute("UPDATE [PurchaseOrders] SET [ArchiveFolder] = ? WHERE [PONumber] = ?", (new_rel, po_number))
    for r in db.query("SELECT [RevID], [SnapshotFile] FROM [OrderRevisions] WHERE [PONumber] = ?", (po_number,)):
        if r.get("SnapshotFile") and r["SnapshotFile"].startswith(old + "/"):
            db.execute("UPDATE [OrderRevisions] SET [SnapshotFile] = ? WHERE [RevID] = ?", (new_rel + r["SnapshotFile"][len(old):], r["RevID"]))
    ensure_po_folder(po_number)
    try:                                                   # remove the old folders only if they are now empty
        for folder, _dirs, _files in os.walk(abs_path(old), topdown=False):
            if not os.listdir(folder):
                os.rmdir(folder)
    except (OSError, ValueError):
        pass
    return moved, missing


def ensure_cert_folder(cert_id):
    """Folder of a certificate: contractor / project / site / certificate number - decided once (Certificates.ArchiveFolder).
    The certificate carries its own contractor and project; an older one that does not is read through its contract.
    If another certificate already owns exactly that path, the certificate id is added."""
    rows = db.query("SELECT * FROM [Certificates] WHERE [CertificateID] = ?", (cert_id,))
    if not rows:
        return None
    g = rows[0]
    rel = g.get("ArchiveFolder")
    if not rel:
        contract = (db.query("SELECT [SubcontractorID], [Project] FROM [Contracts] WHERE [ContractID] = ?", (g["ContractID"],)) or [{}])[0] if g.get("ContractID") else {}
        sub_id = g.get("SubcontractorID") or contract.get("SubcontractorID")
        sub = db.query("SELECT [Name] FROM [Subcontractors] WHERE [SubcontractorID] = ?", (sub_id,)) if sub_id else []
        if not sub:
            return None
        project = ""
        if g.get("ProjectID"):
            p = db.query("SELECT [ProjectName] FROM [Projects] WHERE [ProjectID] = ?", (g["ProjectID"],))
            project = p[0]["ProjectName"] if p else ""
        project = project or contract.get("Project") or "بدون مشروع"
        rel = cert_folder_rel(sub[0]["Name"], project, g.get("SiteName") or "بدون موقع", g["CertificateNo"])
        if db.query("SELECT [CertificateID] FROM [Certificates] WHERE [ArchiveFolder] = ? AND [CertificateID] <> ?", (rel, cert_id)):
            rel = cert_folder_rel(sub[0]["Name"], project, g.get("SiteName") or "بدون موقع", g["CertificateNo"], " (م %s)" % cert_id)
        db.execute("UPDATE [Certificates] SET [ArchiveFolder] = ? WHERE [CertificateID] = ?", (rel, cert_id))
    for sub in CERT_SUBFOLDERS:
        ensure_folder(rel + "/" + sub)
    return ensure_folder(rel)


# ---------------------------------------------------------------- quote comparisons
# المشتريات / مقارنات الأسعار / <المشروع> / <المقارنة> / {العروض الأصلية، تقرير المقارنة، العرض الفائز}
PROC_ROOT = "المشتريات"
COMPARE_DIR = "مقارنات الأسعار"
COMPARE_SUBFOLDERS = ["العروض الأصلية", "تقرير المقارنة", "العرض الفائز"]
QUOTE_DOC_TYPES = ["عرض سعر أصلي", "مرفق إضافي"]
COMPARE_DOC_TYPES = ["تقرير المقارنة", "العرض الفائز", "مرفق إضافي"]
COMPARE_SUBFOLDER = {"عرض سعر أصلي": "العروض الأصلية", "تقرير المقارنة": "تقرير المقارنة", "العرض الفائز": "العرض الفائز", "مرفق إضافي": "العروض الأصلية"}


def ensure_compare_folder(compare_id):
    """Folder of one comparison - decided once (Comparisons.ArchiveFolder); two comparisons with the same name in one project get «(م N)»."""
    rows = db.query("SELECT * FROM [Comparisons] WHERE [CompareID] = ?", (compare_id,))
    if not rows:
        return None
    c = rows[0]
    rel = c.get("ArchiveFolder")
    if not rel:
        base = "/".join([PROC_ROOT, COMPARE_DIR, safe_name(c.get("ProjectName") or "بدون مشروع", maxlen=60), safe_name(c.get("Title") or "مقارنة", maxlen=60)])
        rel = base
        if db.query("SELECT [CompareID] FROM [Comparisons] WHERE [ArchiveFolder] = ? AND [CompareID] <> ?", (rel, compare_id)):
            rel = base + " (م %s)" % compare_id
        db.execute("UPDATE [Comparisons] SET [ArchiveFolder] = ? WHERE [CompareID] = ?", (rel, compare_id))
    for sub in COMPARE_SUBFOLDERS:
        ensure_folder(rel + "/" + sub)
    return ensure_folder(rel)


def compare_doc_folder_rel(folder, doc_type):
    return folder + "/" + COMPARE_SUBFOLDER.get(doc_type, COMPARE_SUBFOLDERS[0])


# ---------------------------------------------------------------- payments and financial movements
# إدارة مقاولي الباطن / <المقاول> / الدفعات والحركات المالية / <السنة> / دفعة رقم N - <التاريخ>      (a payment: a cycle transfer or an executed direct payment)
#                                                                    / <التصنيف> - <التاريخ> - <الوصف>   (materials, refunds, adjustments)
PAYMENTS_DIR = "الدفعات والحركات المالية"
LEDGER_DOC_TYPES = ["سند / إيصال", "فاتورة", "مرفق إضافي"]
PAYMENT_DOC_TYPES = ["معاملة التحويل", "مرفق إضافي"]


def payment_folder_rel(sub_root, pay_no, when):
    return "/".join([sub_root, PAYMENTS_DIR, str(when.year), "دفعة رقم %s - %s" % (pay_no, when.strftime("%Y-%m-%d"))])


def entry_folder_rel(sub_root, entry):
    when = entry.get("EntryDate") or entry.get("CreatedAt") or datetime.now()
    pending = entry["Category"] == "دفعة مباشرة" and entry["Status"] != "منفّذة"
    label = "دفعة مسجلة (لم تُنفَّذ)" if pending else entry["Category"]
    return "/".join([sub_root, PAYMENTS_DIR, str(when.year), safe_name("%s - %s - %s" % (label, when.strftime("%Y-%m-%d"), safe_name(entry["Description"], maxlen=25)), maxlen=80)])


def _sub_root(sub_id):
    rows = db.query("SELECT [Name], [ArchiveFolder] FROM [Subcontractors] WHERE [SubcontractorID] = ?", (sub_id,))
    return None if not rows else (rows[0].get("ArchiveFolder") or sub_root_rel(rows[0]["Name"]))


def payment_folder_for(sub_id, pay_no):
    """The folder of payment N of a contractor - decided once, stored on every record of that payment, created now."""
    root = _sub_root(sub_id)
    if root is None:
        return None
    pays = db.query("SELECT * FROM [Payments] WHERE [SubcontractorID] = ? AND [PayNo] = ?", (sub_id, pay_no))
    ents = db.query("SELECT * FROM [LedgerEntries] WHERE [SubcontractorID] = ? AND [PayNo] = ?", (sub_id, pay_no))
    rel = next((r.get("ArchiveFolder") for r in pays + ents if r.get("ArchiveFolder")), None)
    if not rel:
        dates = [d for d in [r.get("PaidAt") for r in pays] + [e.get("EntryDate") for e in ents] if d]
        rel = payment_folder_rel(root, pay_no, min(dates) if dates else datetime.now())
        db.execute("UPDATE [Payments] SET [ArchiveFolder] = ? WHERE [SubcontractorID] = ? AND [PayNo] = ?", (rel, sub_id, pay_no))
        db.execute("UPDATE [LedgerEntries] SET [ArchiveFolder] = ? WHERE [SubcontractorID] = ? AND [PayNo] = ?", (rel, sub_id, pay_no))
    ensure_folder(rel)
    return rel


def ensure_entry_folder(entry_id):
    """The folder of one manual movement - decided once (LedgerEntries.ArchiveFolder). An executed direct payment lives in its payment folder."""
    rows = db.query("SELECT * FROM [LedgerEntries] WHERE [EntryID] = ?", (entry_id,))
    if not rows:
        return None
    e = rows[0]
    if e.get("PayNo") and e["Status"] == "منفّذة":
        return payment_folder_for(e["SubcontractorID"], e["PayNo"])
    rel = e.get("ArchiveFolder")
    if not rel:
        root = _sub_root(e["SubcontractorID"])
        if root is None:
            return None
        rel = entry_folder_rel(root, e)
        db.execute("UPDATE [LedgerEntries] SET [ArchiveFolder] = ? WHERE [EntryID] = ?", (rel, entry_id))
    return ensure_folder(rel)


def promote_entry_files(entry_id):
    """A direct payment was executed (it now has its number): the files uploaded while it was only recorded move into the payment folder."""
    rows = db.query("SELECT * FROM [LedgerEntries] WHERE [EntryID] = ?", (entry_id,))
    if not rows or not rows[0].get("PayNo"):
        return 0
    e = rows[0]
    target = payment_folder_for(e["SubcontractorID"], e["PayNo"])
    moved = 0
    for d in list_documents("LEDGER", entry_id, current_only=False):
        if d["RelPath"].rsplit("/", 1)[0] != target and relocate_document(d["DocID"], target):
            moved += 1
    db.execute("UPDATE [LedgerEntries] SET [ArchiveFolder] = ? WHERE [EntryID] = ?", (target, entry_id))
    return moved


def cycle_doc_folder_rel(cycle_folder, doc_type):
    return "/".join([cycle_folder, CYCLE_SUBFOLDER.get(doc_type, "الكشوف")])


def ensure_cycle_folder(cycle_id):
    """إدارة مقاولي الباطن / دورات الصرف / <السنة> / دورة رقم N  - decided once (PaymentCycles.ArchiveFolder)."""
    rows = db.query("SELECT [CycleNo], [CreatedAt], [CycleDate], [ArchiveFolder] FROM [PaymentCycles] WHERE [CycleID] = ?", (cycle_id,))
    if not rows:
        return None
    rel = rows[0].get("ArchiveFolder")
    if not rel:
        when = rows[0].get("CycleDate") or rows[0].get("CreatedAt") or datetime.now()
        rel = "/".join([ROOT_SUBS, CYCLES_ROOT, str(when.year), "دورة رقم %s" % rows[0]["CycleNo"]])
        db.execute("UPDATE [PaymentCycles] SET [ArchiveFolder] = ? WHERE [CycleID] = ?", (rel, cycle_id))
    for sub in CYCLE_SUBFOLDERS:
        ensure_folder(rel + "/" + sub)
    return ensure_folder(rel)


def ensure_sub_folder(sub_id, doc_type=None):
    """The contractor's folder, decided once (Subcontractors.ArchiveFolder), with its four paper sub-folders."""
    rows = db.query("SELECT [Name], [ArchiveFolder] FROM [Subcontractors] WHERE [SubcontractorID] = ?", (sub_id,))
    if not rows:
        return None
    root = rows[0].get("ArchiveFolder")
    if not root:
        root = sub_root_rel(rows[0]["Name"])
        db.execute("UPDATE [Subcontractors] SET [ArchiveFolder] = ? WHERE [SubcontractorID] = ?", (root, sub_id))
    for sub in SUB_PAPER_FOLDERS:
        ensure_folder("/".join([root, SUB_PAPERS, sub]))
    ensure_folder(root + "/المستخلصات")
    return sub_doc_folder_rel(root, doc_type) if doc_type else root


def ensure_supplier_folder(supplier_id, doc_type=None):
    """The supplier's folder is decided once (Suppliers.ArchiveFolder). With a document type it returns the matching
    sub-folder inside  أوراق المورد, creating the four sub-folders so the structure is always complete."""
    rows = db.query("SELECT [Name], [ArchiveFolder] FROM [Suppliers] WHERE [SupplierID] = ?", (supplier_id,))
    if not rows:
        return None
    root = rows[0].get("ArchiveFolder")
    if not root:
        root = supplier_root_rel(rows[0]["Name"])
        db.execute("UPDATE [Suppliers] SET [ArchiveFolder] = ? WHERE [SupplierID] = ?", (root, supplier_id))
    for sub in ("ملف تأهيلي", "كتالوج", "شهادات", "ملفات أخرى"):
        ensure_folder("/".join([root, SUPPLIER_PAPERS, sub]))
    return supplier_doc_folder_rel(root, doc_type) if doc_type else root


# --------------------------------------------------------------------------- writing files
def _ext(name):
    return name.rsplit(".", 1)[-1].lower() if name and "." in name else ""


def _unique(path):
    """Never overwrite: if a file with this name already exists (two uploads in the same second), add (2), (3) ..."""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.exists("%s (%d)%s" % (base, n, ext)):
        n += 1
    return "%s (%d)%s" % (base, n, ext)


def _write(dest, source, keep_source=False):
    if hasattr(source, "save"):            # werkzeug FileStorage
        source.save(dest)
    elif isinstance(source, (bytes, bytearray)):   # generated content (the PDF / data copy of an order)
        with open(dest, "wb") as f:
            f.write(source)
    elif keep_source:                       # a file that must stay where it is (migration of old uploads)
        shutil.copy2(source, dest)
    else:                                   # a path already on disk (pending upload) - move it
        shutil.move(source, dest)


def store_document(entity_type, entity_key, folder_rel, doc_type, source, original_name, note="", user="",
                   expiry=None, keep_source=False, exact_name=None, system=False):
    """Saves one document into the entity's folder and records it. Returns the new Documents row (dict)."""
    ext = _ext(original_name)
    if ext not in ALLOWED_EXT and not system:           # people may upload only these; the system writes .json too
        raise UnsupportedFile(original_name)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = safe_name(os.path.splitext(original_name)[0], fallback="ملف", maxlen=30)
    stored = safe_name(exact_name, maxlen=120) if exact_name else "%s - %s - %s.%s" % (safe_name(doc_type, maxlen=20), stamp, base, ext)
    ensure_folder(folder_rel)
    dest = _unique(os.path.join(abs_path(folder_rel), stored))
    stored = os.path.basename(dest)
    _write(dest, source, keep_source)
    rel = "%s/%s" % (folder_rel, stored)
    key = str(entity_key)
    if doc_type not in MULTI_TYPES:
        db.execute(
            "UPDATE [Documents] SET [IsCurrent] = False WHERE [EntityType] = ? AND [EntityKey] = ? AND [DocType] = ?",
            (entity_type, key, doc_type),
        )
    doc_id = db.execute_insert(
        "INSERT INTO [Documents] ([EntityType], [EntityKey], [DocType], [StoredName], [OriginalName], [RelPath], "
        "[UploadedAt], [UploadedBy], [Note], [IsCurrent], [ExpiryDate]) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, True, ?)",
        (entity_type, key, doc_type, stored, original_name, rel, datetime.now(), user or "", note or "", expiry),
    )
    legacy = LEGACY_COLUMN.get((entity_type, doc_type))
    if legacy:
        table, column, keycol, numeric = legacy
        db.execute("UPDATE [%s] SET [%s] = ? WHERE [%s] = ?" % (table, column, keycol),
                   (rel, int(key) if numeric else key))
    return {"DocID": doc_id, "RelPath": rel, "StoredName": stored}


def _locate(rel):
    """(absolute path, keep_original) of a stored file: in the archive (movable), or in the old uploads folder of the
    very first versions (read-only: it is copied, never removed)."""
    parts = [x for x in (rel or "").replace("\\", "/").split("/") if x and x != ".."]
    for base, keep in ((config.ARCHIVE_ROOT, False), (config.LEGACY_UPLOADS, True)):
        root = os.path.normpath(base)
        p = os.path.normpath(os.path.join(root, *parts))
        if p.startswith(root + os.sep) and os.path.isfile(p):
            return p, keep
    return None, False


def relocate_document(doc_id, new_folder_rel, new_name=None):
    """Moves a stored file to another folder of the archive and fixes its record. COPY -> verify -> update the database
    -> only then remove the original, so a failure at any step never leaves a record pointing at nothing."""
    doc = get_document(doc_id)
    if not doc:
        return False
    src, keep_original = _locate(doc["RelPath"])
    if not src:
        return False
    ensure_folder(new_folder_rel)
    dest = _unique(os.path.join(abs_path(new_folder_rel), storable_filename(new_name or os.path.basename(src))))
    if os.path.normpath(dest) == os.path.normpath(src):
        return True
    shutil.copy2(src, dest)
    if os.path.getsize(dest) != os.path.getsize(src):
        os.remove(dest)
        return False
    rel = "%s/%s" % (new_folder_rel, os.path.basename(dest))
    db.execute("UPDATE [Documents] SET [RelPath] = ?, [StoredName] = ? WHERE [DocID] = ?", (rel, os.path.basename(dest), doc_id))
    legacy = LEGACY_COLUMN.get((doc["EntityType"], doc["DocType"]))
    if legacy and doc.get("IsCurrent"):
        table, column, keycol, numeric = legacy
        key = doc["EntityKey"]
        db.execute("UPDATE [%s] SET [%s] = ? WHERE [%s] = ?" % (table, column, keycol), (rel, int(key) if numeric else key))
    if not keep_original:
        try:
            os.remove(src)
        except OSError:
            pass                                # the record is already right; a leftover copy is harmless
    return True


def save_profile_file(file_storage, folder_rel, label):
    """Supplier / subcontractor / contract profile files (CR, tax certificate, catalog ...). One path is
    stored in the entity's own column, exactly as before - only the location is now in the archive."""
    if not file_storage or not file_storage.filename:
        return None
    ext = _ext(file_storage.filename)
    if ext not in ALLOWED_EXT:
        raise UnsupportedFile(file_storage.filename)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = safe_name(os.path.splitext(file_storage.filename)[0], fallback="ملف", maxlen=30)
    stored = "%s - %s - %s.%s" % (safe_name(label, maxlen=20), stamp, base, ext)
    ensure_folder(folder_rel)
    dest = _unique(os.path.join(abs_path(folder_rel), stored))
    file_storage.save(dest)
    return "%s/%s" % (folder_rel, os.path.basename(dest))


# --------------------------------------------------------------------------- reading
def list_documents(entity_type, entity_key, current_only=True):
    sql = ("SELECT [DocID], [DocType], [StoredName], [OriginalName], [RelPath], [UploadedAt], [UploadedBy], "
           "[Note], [IsCurrent], [ExpiryDate] FROM [Documents] WHERE [EntityType] = ? AND [EntityKey] = ?")
    if current_only:
        sql += " AND [IsCurrent] = True"
    return db.query(sql + " ORDER BY [UploadedAt] DESC, [DocID] DESC", (entity_type, str(entity_key)))


def list_documents_many(entity_type):
    """Every current document of one entity type (e.g. all the files of the manual movements) - one query for a whole page."""
    return [d for d in db.query("SELECT * FROM [Documents] WHERE [EntityType] = ?", (entity_type,)) if d.get("IsCurrent")]


def get_document(doc_id):
    rows = db.query(
        "SELECT [DocID], [EntityType], [EntityKey], [DocType], [StoredName], [OriginalName], [RelPath], [IsCurrent], [ExpiryDate], [UploadedAt] "
        "FROM [Documents] WHERE [DocID] = ?", (doc_id,))
    return rows[0] if rows else None


def expiry_state(expiry, warn_days=30):
    """None (no date) | ("expired", days_ago) | ("soon", days_left) | ("ok", days_left)."""
    if not expiry:
        return None
    days = (expiry.date() - datetime.now().date()).days
    return ("expired", -days) if days < 0 else (("soon", days) if days <= warn_days else ("ok", days))


def abs_path_exists(rel):
    """True if the file of a stored relative path is in the archive (or the old uploads folder)."""
    parts = [x for x in (rel or "").replace("\\", "/").split("/") if x and x != ".."]
    for base in (config.ARCHIVE_ROOT, config.LEGACY_UPLOADS):
        p = os.path.normpath(os.path.join(base, *parts))
        if p.startswith(os.path.normpath(base) + os.sep) and os.path.isfile(p):
            return True
    return False


def move_file(rel, new_folder_rel):
    """Moves a file inside the archive (copy -> check -> remove) and returns its new relative path (None if missing)."""
    src, keep_original = _locate(rel)
    if not src:
        return None
    ensure_folder(new_folder_rel)
    dest = _unique(os.path.join(abs_path(new_folder_rel), storable_filename(os.path.basename(src))))
    if os.path.normpath(dest) == os.path.normpath(src):
        return rel
    shutil.copy2(src, dest)
    if os.path.getsize(dest) != os.path.getsize(src):
        os.remove(dest)
        return None
    if not keep_original:
        try:
            os.remove(src)
        except OSError:
            pass
    return "%s/%s" % (new_folder_rel, os.path.basename(dest))


def store_generated(entity_type, key, folder_rel, doc_type, content, name, user=""):
    """A file the SYSTEM wrote (a cycle sheet, a payment order): written once per name - writing it again REPLACES the
    same file and record (so «print again» never piles up copies). Returns the record."""
    stored = safe_name(name, maxlen=120)
    for d in list_documents(entity_type, key):
        if d["DocType"] == doc_type and d.get("IsCurrent") and d["StoredName"] == stored:
            try:
                with open(abs_path(d["RelPath"]), "wb") as f:
                    f.write(content)
                db.execute("UPDATE [Documents] SET [UploadedAt] = ?, [UploadedBy] = ? WHERE [DocID] = ?", (datetime.now(), user or "", d["DocID"]))
                return d
            except (OSError, ValueError):
                break
    return store_document(entity_type, key, folder_rel, doc_type, content, name, user=user, exact_name=name, system=True)


def discard_file(rel):
    """Deletes a file this request has JUST written when the database save that needed it failed (no record points at it)."""
    if not rel:
        return
    try:
        os.remove(abs_path(rel))
    except (OSError, ValueError):
        pass


def discard_document(doc_id):
    """Removes a record AND its file - only for a file the system has just written and that no saved record needs
    (cleaning up after a failed save). Never used on anything a person uploaded."""
    doc = get_document(doc_id)
    if not doc:
        return
    try:
        os.remove(abs_path(doc["RelPath"]))
    except (OSError, ValueError):
        pass
    db.execute("DELETE FROM [Documents] WHERE [DocID] = ?", (doc_id,))


def hide_document(doc_id):
    """Takes a wrongly uploaded file off the list WITHOUT deleting it (it stays on disk and under «النسخ السابقة»)."""
    db.execute("UPDATE [Documents] SET [IsCurrent] = False WHERE [DocID] = ?", (doc_id,))


def panel(entity_type, entity_key, doc_types):
    """Data for the documents panel: one slot per document type with its current file(s) and older versions."""
    docs = list_documents(entity_type, entity_key, current_only=False)
    slots = []
    for t in doc_types:
        mine = [d for d in docs if d["DocType"] == t]
        slots.append({
            "type": t,
            "multi": t in MULTI_TYPES,
            "current": [dict(d, expiry_state=expiry_state(d.get("ExpiryDate"))) for d in mine if d.get("IsCurrent")],
            "history": [d for d in mine if not d.get("IsCurrent")],
        })
    return slots


def send(rel_path, as_attachment=False, download_name=None):
    """Serves a stored file: from the archive, or (read-only) from the old uploads folder."""
    rel = (rel_path or "").replace("\\", "/").lstrip("/")
    parts = [x for x in rel.split("/") if x and x != ".."]
    for base in (config.ARCHIVE_ROOT, config.LEGACY_UPLOADS):
        root = os.path.normpath(base)
        p = os.path.normpath(os.path.join(root, *parts))
        if p.startswith(root + os.sep) and os.path.isfile(p):
            return send_file(p, as_attachment=as_attachment, download_name=download_name or os.path.basename(p))
    from flask import render_template
    return render_template("error.html", code=404, title="الملف المسجَّل غير موجود على القرص",
                           text="النظام يعرف أن لهذا السجل ملفًا (%s) لكنه ليس في مجلد الأرشيف. غالبًا نُقل أو حُذف من الخارج. أعد رفعه من الصفحة، "
                                "أو أرسل هذا المسار لمسؤول النظام. فحص شامل للأرشيف: الإعدادات ← صحة الأرشيف." % (rel or "بلا مسار")), 404


# --------------------------------------------------------------------------- pending uploads (quote being read)
def purge_pending(days=3):
    try:
        folder = abs_path(PENDING)
        limit = time.time() - days * 86400
        for n in os.listdir(folder):
            p = os.path.join(folder, n)
            if os.path.isfile(p) and os.path.getmtime(p) < limit:
                os.remove(p)
    except OSError:
        pass


def save_pending(file_storage):
    """A quote file chosen BEFORE the order exists. Returns only a token (the file name) - never a path,
    so the browser can't point the server at an arbitrary file."""
    ext = _ext(file_storage.filename)
    if ext not in ALLOWED_EXT:
        raise UnsupportedFile(file_storage.filename)
    ensure_folder(PENDING)
    purge_pending()
    name = "%s_%s.%s" % (datetime.now().strftime("%Y%m%d_%H%M%S"),
                         safe_name(os.path.splitext(file_storage.filename)[0], fallback="ملف", maxlen=40), ext)
    file_storage.save(os.path.join(abs_path(PENDING), name))
    return name


def pending_path(token):
    """Absolute path of a pending file by its token, or None if it is not a real file inside the pending folder."""
    if not token:
        return None
    folder = abs_path(PENDING)
    p = os.path.normpath(os.path.join(folder, os.path.basename(token)))
    return p if p.startswith(folder + os.sep) and os.path.isfile(p) else None


# --------------------------------------------------------------------------- health probe
def storage_probe():
    """Writes, reads and deletes a tiny file in the archive root; returns timings (used by /health and
    tools/check_storage.py during the field test)."""
    out = {"ok": False, "write_ms": None, "read_ms": None}
    try:
        folder = ensure_folder("_health") and abs_path("_health")
        p = os.path.join(folder, "probe_%d_%d.txt" % (os.getpid(), threading.get_ident()))
        t0 = time.perf_counter()
        with open(p, "w", encoding="utf-8") as f:
            f.write("ok " * 64)
        t1 = time.perf_counter()
        with open(p, "r", encoding="utf-8") as f:
            f.read()
        t2 = time.perf_counter()
        os.remove(p)
        out.update(ok=True, write_ms=round((t1 - t0) * 1000, 1), read_ms=round((t2 - t1) * 1000, 1))
    except OSError as e:
        out["error"] = str(e)
    return out

# -*- coding: utf-8 -*-
"""
Documents of purchase orders and certificates - ONE upload route and ONE open/download route for all of
them (replaces the seven separate file-serving routes of v9). Replacing a document never deletes the
old one: it becomes a previous version.
"""
from datetime import datetime
from urllib.parse import urlparse

from flask import abort, flash, redirect, request, url_for

import archive
import auth
import db
from routes import route

# entity type -> (allowed document types, function that returns/creates the entity's archive folder)
ENTITIES = {
    "PO": (archive.PO_DOC_TYPES, lambda key, doc_type: archive.po_doc_folder_rel(archive.ensure_po_folder(key), doc_type)),
    "CERT": (archive.CERT_DOC_TYPES, lambda key, doc_type: archive.cert_doc_folder_rel(archive.ensure_cert_folder(int(key)), doc_type)),
    "SUB": (archive.SUB_DOC_TYPES, lambda key, doc_type: archive.ensure_sub_folder(int(key), doc_type)),
    "CONTRACT": (archive.CONTRACT_DOC_TYPES, lambda key, doc_type: archive.ensure_contract_folder(int(key))),
    "CYCLE": (archive.CYCLE_DOC_TYPES, lambda key, doc_type: archive.cycle_doc_folder_rel(archive.ensure_cycle_folder(int(key)), doc_type)),
    "SUPPLIER": (archive.SUPPLIER_DOC_TYPES, lambda key, doc_type: archive.ensure_supplier_folder(int(key), doc_type)),
    "LEDGER": (archive.LEDGER_DOC_TYPES, lambda key, doc_type: archive.ensure_entry_folder(int(key))),
    "PAYMENT": (archive.PAYMENT_DOC_TYPES, lambda key, doc_type: archive.payment_folder_for(*[int(x) for x in key.split(":")])),
    "QUOTE": (archive.QUOTE_DOC_TYPES, lambda key, doc_type: archive.compare_doc_folder_rel(archive.ensure_compare_folder(int(db.query("SELECT [CompareID] FROM [Quotes] WHERE [QuoteID] = ?", (int(key),))[0]["CompareID"])), doc_type)),
    "COMPARE": (archive.COMPARE_DOC_TYPES, lambda key, doc_type: archive.compare_doc_folder_rel(archive.ensure_compare_folder(int(key)), doc_type)),
}
BACK_TO = {"PO": "orders_list", "CERT": "certificates_list", "SUPPLIER": "suppliers_list", "SUB": "subcontractors_list", "CYCLE": "cycles_list", "CONTRACT": "subcontractors_list",
           "LEDGER": "subcontractors_list", "PAYMENT": "cycles_list", "QUOTE": "compare_list", "COMPARE": "compare_list"}


def back(default_endpoint, **kw):
    """Redirect to the page the user came from - only if it is on this same server."""
    ref = request.referrer
    if ref and urlparse(ref).netloc == request.host:
        return redirect(ref)
    return redirect(url_for(default_endpoint, **kw))


def is_locked(entity_type, entity_key):
    """A payment transaction (certificate) closed in the archive keeps its five papers as they are."""
    if entity_type != "CERT":
        return False
    try:
        rows = db.query("SELECT [ClosedAt] FROM [Certificates] WHERE [CertificateID] = ?", (int(entity_key),))
    except (ValueError, TypeError):
        return False
    return bool(rows and rows[0].get("ClosedAt"))


def save_entity_document(entity_type, entity_key, doc_type, file_storage, note="", expiry=None):
    """Used by the upload route and by the small shortcut routes. Returns the stored row, or None after
    flashing the reason."""
    if entity_type not in ENTITIES:
        abort(400)
    types, ensure = ENTITIES[entity_type]
    if doc_type not in types:
        flash("نوع المستند غير معروف.")
        return None
    if not file_storage or not file_storage.filename:
        flash("اختر ملفًا أولًا.")
        return None
    if is_locked(entity_type, entity_key):
        flash("هذه المعاملة مُقفلة في الأرشيف. أعد فتحها أولًا (الأرشيف ← المعاملة ← إعادة فتح) ثم أضف الملف.")
        return None
    try:
        folder = ensure(entity_key, doc_type)
    except (ValueError, TypeError):
        folder = None
    if not folder:
        flash("لم يتم العثور على السجل.")
        return None
    user = (auth.current_user() or {}).get("Username", "")
    try:
        return archive.store_document(entity_type, entity_key, folder, doc_type, file_storage,
                                      file_storage.filename, note, user, expiry=expiry)
    except archive.UnsupportedFile:
        flash("صيغة الملف غير مدعومة (المسموح: PDF، صور JPG/PNG، Word، Excel).")
        return None
    except archive.PathTooLong as e:
        flash(str(e))
        return None


def save_profile(file_storage, folder_rel, label):
    """Profile files (supplier / subcontractor / contract papers): stored in the archive, path kept in the
    entity's own column. Returns the stored relative path, or None."""
    if not file_storage or not file_storage.filename:
        return None
    try:
        return archive.save_profile_file(file_storage, folder_rel, label)
    except archive.UnsupportedFile:
        flash("صيغة الملف غير مدعومة: %s" % file_storage.filename)
        return None
    except archive.PathTooLong as e:
        flash(str(e))
        return None


@route("/documents/upload", methods=["POST"])
@auth.login_required
def document_upload():
    entity_type = request.form.get("entity_type", "")
    entity_key = request.form.get("entity_key", "").strip()
    doc_type = request.form.get("doc_type", "")
    expiry = None
    if request.form.get("expiry"):
        try:
            expiry = datetime.strptime(request.form.get("expiry"), "%Y-%m-%d")
        except ValueError:
            flash("تاريخ الانتهاء غير صحيح - حُفظ المستند بدونه.")
    doc = save_entity_document(entity_type, entity_key, doc_type, request.files.get("file"),
                               request.form.get("note", "").strip(), expiry)
    if doc:
        flash("تم حفظ المستند في الأرشيف: %s" % doc_type)
    return back(BACK_TO.get(entity_type, "dashboard"))


@route("/documents/<int:doc_id>/hide", methods=["POST"])
@auth.login_required
def document_hide(doc_id):
    """Removes a wrongly uploaded file from the list; nothing is deleted from disk."""
    doc = archive.get_document(doc_id)
    if not doc:
        abort(404)
    if is_locked(doc["EntityType"], doc["EntityKey"]):
        flash("هذه المعاملة مُقفلة في الأرشيف. أعد فتحها أولًا لإزالة مستند منها.")
        return back(BACK_TO.get(doc["EntityType"], "dashboard"))
    archive.hide_document(doc_id)
    flash("أُزيل المستند من القائمة (الملف نفسه محفوظ في «النسخ السابقة»).")
    return back(BACK_TO.get(doc["EntityType"], "dashboard"))


@route("/documents/<int:doc_id>")
@auth.login_required
def document_view(doc_id):
    doc = archive.get_document(doc_id)
    if not doc:
        abort(404)
    return archive.send(doc["RelPath"], as_attachment=bool(request.args.get("download")),
                        download_name=doc["OriginalName"])

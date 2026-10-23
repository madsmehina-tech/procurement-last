# -*- coding: utf-8 -*-
"""
payment_files.py - the files of payments and financial movements go to the archive:

    إدارة مقاولي الباطن / <المقاول> / الدفعات والحركات المالية / <السنة> / دفعة رقم N - <التاريخ>

A cycle transfer: the scan the user makes (the signed payment order + the bank transfer picture) is kept ONCE in the payment's
folder, and a copy goes into the folder of EVERY certificate paid by that transfer as «سند الصرف» - the fifth paper of
the certificate's transaction. A manual movement keeps its own files (receipt, invoice) in its folder.
"""
import archive
import cycles_service as cy
import db


class FileError(Exception):
    """The message is shown to the person as is."""


def bad_file(files):
    """The first file whose type is not accepted (checked BEFORE anything is saved)."""
    return next((f.filename for f in files if archive._ext(f.filename) not in archive.ALLOWED_EXT), None)


def save_entry_files(entry_id, files, doc_type, note, user):
    """Files of one manual movement (receipt, invoice ...). Returns how many were saved."""
    if doc_type not in archive.LEDGER_DOC_TYPES:
        doc_type = archive.LEDGER_DOC_TYPES[0]
    bad = bad_file(files)
    if bad:
        raise FileError("صيغة الملف غير مدعومة: %s (المسموح: PDF، صور، Word، Excel)." % bad)
    folder = archive.ensure_entry_folder(entry_id)
    if not folder:
        raise FileError("تعذّر تحديد فولدر الحركة.")
    for f in files:
        f.stream.seek(0)
        archive.store_document("LEDGER", entry_id, folder, doc_type, f, f.filename, note or "", user)
    return len(files)


def after_execution(cycle_id, sub_id):
    """Right after «تم التحويل»: the payment's folder exists, and the files that recorded direct payments of the contractor
    already had move into it. Returns the payment number and its folder."""
    pay_no = cy.group_pay_no(cycle_id, sub_id)
    if not pay_no:
        return None, None
    folder = archive.payment_folder_for(int(sub_id), pay_no)
    for e in db.query("SELECT * FROM [LedgerEntries] WHERE [CycleID] = ? AND [SubcontractorID] = ?", (cycle_id, int(sub_id))):
        if e.get("PayNo") == pay_no and e["Status"] == "منفّذة":
            archive.promote_entry_files(e["EntryID"])
    return pay_no, folder


def store_transfer_files(cycle_id, sub_id, files, note, user):
    """The user's scan(s) of the executed transfer: once in the payment folder, a copy in each paid certificate (as «سند الصرف»)."""
    bad = bad_file(files)
    if bad:
        raise FileError("صيغة الملف غير مدعومة: %s (المسموح: PDF، صور، Word، Excel)." % bad)
    data = cy.load(cycle_id)
    gp = next((g for g in (data or {"groups": []})["groups"] if g["sub_id"] == int(sub_id)), None)
    pay_no = cy.group_pay_no(cycle_id, sub_id)
    if not gp or not pay_no:
        raise FileError("لا تحويل منفّذ لهذا المقاول في الدورة بعد.")
    paid = [l["CertificateID"] for l in gp["lines"] if l["Payable"] and not l["IsEntry"] and l["Exec"] == cy.EX_DONE]
    folder = archive.payment_folder_for(int(sub_id), pay_no)
    for f in files:
        f.stream.seek(0)
        archive.store_document("PAYMENT", "%s:%s" % (sub_id, pay_no), folder, "معاملة التحويل", f, f.filename, note or "", user)
        for cert_id in paid:
            f.stream.seek(0)
            cert_folder = archive.cert_doc_folder_rel(archive.ensure_cert_folder(cert_id), "سند الصرف")
            doc = archive.store_document("CERT", cert_id, cert_folder, "سند الصرف", f, f.filename, note or "", user)
            db.execute("UPDATE [Payments] SET [DocID] = ? WHERE [CycleID] = ? AND [CertificateID] = ? AND [DocID] IS NULL", (doc["DocID"], cycle_id, cert_id))
    return len(files)

# -*- coding: utf-8 -*-
"""
contract_pdf.py - DOCX -> PDF. The best result (identical to what Word shows) comes from Microsoft Word itself, which the master PC has;
LibreOffice is the second choice; with neither the PDF is not produced and the person is told (the DOCX is always produced).

config.ini:   [contracts]  pdf_backend = auto | word | libreoffice | none        (default auto: Word, then LibreOffice)
              [contracts]  libreoffice_path = C:\\Program Files\\LibreOffice\\program\\soffice.exe      (only if it is not found by itself)
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading

_LOCK = threading.Lock()                                # Word / LibreOffice convert one file at a time


class PdfUnavailable(Exception):
    """No converter could make the PDF; the message tells why."""


def _setting(name, default=""):
    try:
        import config
        return config._get("contracts", name, "PO_CONTRACTS_" + name.upper(), default)
    except Exception:                                   # noqa
        return default


def _word(docx, pdf):
    if sys.platform != "win32":
        raise PdfUnavailable("Word غير متاح على هذا الجهاز")
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        raise PdfUnavailable("مكتبة pywin32 غير مثبتة (لازم لتحويل PDF بواسطة Word): pip install pywin32")
    pythoncom.CoInitialize()
    app = None
    try:
        app = win32com.client.DispatchEx("Word.Application")
        app.Visible = False
        app.DisplayAlerts = 0
        d = app.Documents.Open(os.path.abspath(docx), ReadOnly=True, AddToRecentFiles=False, Visible=False)
        d.ExportAsFixedFormat(OutputFileName=os.path.abspath(pdf), ExportFormat=17)         # 17 = PDF
        d.Close(False)
    except Exception as e:                              # noqa
        raise PdfUnavailable("تعذّر التحويل بواسطة Word: %s" % " ".join(str(e).split())[:140])
    finally:
        try:
            if app is not None:
                app.Quit()
        except Exception:                               # noqa
            pass
        pythoncom.CoUninitialize()


def _soffice():
    p = _setting("libreoffice_path")
    if p and os.path.isfile(p):
        return p
    for name in ("soffice", "libreoffice", "soffice.exe"):
        found = shutil.which(name)
        if found:
            return found
    for guess in (r"C:\Program Files\LibreOffice\program\soffice.exe", r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"):
        if os.path.isfile(guess):
            return guess
    return None


def _libreoffice(docx, pdf):
    exe = _soffice()
    if not exe:
        raise PdfUnavailable("LibreOffice غير مثبّت")
    out = tempfile.mkdtemp(prefix="po_pdf_")
    profile = tempfile.mkdtemp(prefix="po_lo_")
    try:
        subprocess.run([exe, "--headless", "-env:UserInstallation=file:///%s" % profile.replace("\\", "/").lstrip("/"), "--convert-to", "pdf", "--outdir", out, os.path.abspath(docx)],
                       check=True, capture_output=True, timeout=120)
        made = os.path.join(out, os.path.splitext(os.path.basename(docx))[0] + ".pdf")
        if not os.path.isfile(made):
            raise PdfUnavailable("LibreOffice لم ينتج ملف PDF")
        shutil.move(made, pdf)
    except subprocess.SubprocessError as e:
        raise PdfUnavailable("تعذّر التحويل بواسطة LibreOffice: %s" % " ".join(str(e).split())[:120])
    finally:
        shutil.rmtree(out, ignore_errors=True)
        shutil.rmtree(profile, ignore_errors=True)


_STUCK = {"thread": None}


def convert(docx, pdf, timeout=90):
    """Makes `pdf` from `docx`. Returns the name of the converter used.
    Never waits forever: a Word that hangs on a dialog is given `timeout` seconds; while a hung conversion is still alive no new one is started
    (the person is told to close the Word window that is waiting)."""
    mode = (_setting("pdf_backend", "auto") or "auto").lower()
    if mode == "none":
        raise PdfUnavailable("تحويل PDF متوقف في الإعدادات")
    order = {"word": ["word"], "libreoffice": ["libreoffice"]}.get(mode, ["word", "libreoffice"])
    if not _LOCK.acquire(timeout=timeout):
        raise PdfUnavailable("محوّل PDF مشغول بعملية أخرى؛ أعد المحاولة بعد قليل")
    errors = []
    try:
        stuck = _STUCK["thread"]
        if stuck is not None and stuck.is_alive():
            raise PdfUnavailable("تحويل سابق ما زال عالقًا (غالبًا نافذة مفتوحة داخل Word): أغلق نافذة Word الظاهرة ثم أعد المحاولة")
        for name in order:
            box = {}

            def work(_name=name, _box=box):
                try:
                    (_word if _name == "word" else _libreoffice)(docx, pdf)
                    _box["ok"] = os.path.isfile(pdf)
                except PdfUnavailable as e:
                    _box["err"] = str(e)
                except Exception as e:                        # noqa
                    _box["err"] = "خطأ غير متوقع: %s" % " ".join(str(e).split())[:120]
            t = threading.Thread(target=work, name="po-pdf-" + name, daemon=True)
            t.start()
            t.join(timeout)
            if t.is_alive():
                _STUCK["thread"] = t
                errors.append("%s: انتهت المهلة (%d ثانية) - غالبًا نافذة مفتوحة داخل Word تنتظر ردك" % (name, timeout))
                break
            if box.get("ok"):
                return name
            errors.append("%s: %s" % (name, box.get("err") or "لم يُنتج ملف"))
    finally:
        _LOCK.release()
    raise PdfUnavailable(" | ".join(errors) or "لا محوّل متاح")

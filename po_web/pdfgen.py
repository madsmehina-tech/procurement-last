# -*- coding: utf-8 -*-
"""
pdfgen.py - HTML -> PDF with the browser that is already on the PC (Microsoft Edge or Google Chrome, no window shown).

Why a browser: it prints Arabic exactly as the screen shows it (joined letters, right-to-left tables, the company logo),
which small PDF libraries do not. Nothing is installed; if no browser is found the order is still saved and the
printable page still works - only the automatic PDF is skipped, and the order page says so.
"""
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import config


class PdfError(Exception):
    pass


def _candidates():
    if sys.platform.startswith("win"):
        pf, pf86, local = (os.environ.get(k, "") for k in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"))
        for base in (pf86, pf, local):
            for rel in ("Microsoft\\Edge\\Application\\msedge.exe", "Google\\Chrome\\Application\\chrome.exe"):
                if base:
                    yield os.path.join(base, rel)
    for name in ("msedge", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            yield found


def find_browser():
    if config.PDF_MODE == "off":
        return None
    if config.PDF_BROWSER:
        return config.PDF_BROWSER if os.path.isfile(config.PDF_BROWSER) else None
    return next((p for p in _candidates() if p and os.path.isfile(p)), None)


def available():
    return bool(find_browser())


def html_to_pdf(html, out_path, timeout=90):
    """Writes the PDF of `html` to out_path. Raises PdfError with a plain reason."""
    browser = find_browser()
    if not browser:
        raise PdfError("لم يُعثر على Microsoft Edge أو Google Chrome على هذا الجهاز")
    work = tempfile.mkdtemp(prefix="po_pdf_")
    try:
        src, tmp_pdf, profile = (os.path.join(work, n) for n in ("doc.html", "out.pdf", "profile"))
        with open(src, "w", encoding="utf-8") as f:
            f.write(html)
        cmd = [browser, "--headless", "--disable-gpu", "--no-pdf-header-footer", "--print-to-pdf-no-header",
               "--no-first-run", "--disable-extensions", "--user-data-dir=" + profile, "--print-to-pdf=" + tmp_pdf]
        cmd += shlex.split(config.PDF_ARGS or "", posix=not sys.platform.startswith("win"))
        cmd.append(Path(src).as_uri())
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=flags)
        except subprocess.TimeoutExpired:
            raise PdfError("انتهت مهلة إنشاء PDF (%d ثانية)" % timeout)
        except OSError as e:
            raise PdfError("تعذّر تشغيل المتصفح: %s" % e)
        if not os.path.isfile(tmp_pdf) or os.path.getsize(tmp_pdf) < 1000:
            tail = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
            raise PdfError("لم يُنتج المتصفح ملف PDF %s" % tail[0][:120])
        with open(tmp_pdf, "rb") as f:
            if f.read(5) != b"%PDF-":
                raise PdfError("الملف الناتج ليس PDF صالحًا")
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        shutil.move(tmp_pdf, out_path)
    finally:
        shutil.rmtree(work, ignore_errors=True)

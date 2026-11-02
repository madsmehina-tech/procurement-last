# -*- coding: utf-8 -*-
"""v27.7 - uploads are checked for their STRUCTURE, not only their first bytes."""
import io
import zipfile

import pytest

import archive


def _zip(parts, compress=zipfile.ZIP_DEFLATED):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compress) as z:
        for name, data in parts.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_real_word_and_excel_files_pass():
    from docx import Document
    from openpyxl import Workbook
    d = io.BytesIO()
    doc = Document()
    doc.add_paragraph("عقد")
    doc.save(d)
    archive.check_content("docx", d.getvalue())
    x = io.BytesIO()
    Workbook().save(x)
    archive.check_content("xlsx", x.getvalue())


@pytest.mark.parametrize("ext,data,why", [
    ("pdf", b"%PDF-1.4 a scan that stopped in the middle " + b"x" * 5000, "%%EOF"),
    ("png", b"\x89PNG\r\n\x1a\n" + b"x" * 100, "PNG"),
    ("jpg", b"\xff\xd8\xff\xe0" + b"x" * 200, "JPEG"),
    ("docx", _zip({"hello.txt": "a plain zip"}), "Word"),
    ("xlsx", _zip({"[Content_Types].xml": "<x/>", "word/document.xml": "<w/>"}), "Excel"),
    ("docx", b"PK\x03\x04 this is not really a zip", "تالف"),
    ("docx", _zip({"[Content_Types].xml": "<x/>", "word/document.xml": "<w/>", "../evil.txt": "x"}), "غير آمنة"),
    ("xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 10, "أصغر"),
])
def test_damaged_or_fake_files_are_refused_with_a_clear_reason(ext, data, why):
    with pytest.raises(archive.MismatchedFile) as e:
        archive.check_content(ext, data)
    assert why in str(e.value)


def test_a_zip_bomb_posing_as_a_word_file_is_refused(monkeypatch):
    monkeypatch.setattr(archive, "MAX_UNPACKED_BYTES", 5 * 1024 * 1024)
    bomb = _zip({"[Content_Types].xml": "<x/>", "word/document.xml": "<w/>", "word/big.bin": b"\x00" * (20 * 1024 * 1024)})
    assert len(bomb) < 100 * 1024                                       # tiny on disk ...
    with pytest.raises(archive.MismatchedFile) as e:
        archive.check_content("docx", bomb)                              # ... huge when unpacked
    assert "ملغوماً" in str(e.value)


def test_an_upload_stream_is_checked_without_being_consumed(tmp_path):
    from werkzeug.datastructures import FileStorage
    good = FileStorage(stream=io.BytesIO(b"%PDF-1.4 body\n%%EOF"), filename="a.pdf")
    archive.check_content("pdf", good)
    assert good.stream.read() == b"%PDF-1.4 body\n%%EOF"               # still whole afterwards: the save that follows gets every byte


def test_a_file_on_disk_is_checked_too(tmp_path):
    p = tmp_path / "a.pdf"
    p.write_bytes(b"%PDF-1.4 body\n%%EOF")
    archive.check_content("pdf", str(p))
    p.write_bytes(b"%PDF-1.4 cut")
    with pytest.raises(archive.MismatchedFile):
        archive.check_content("pdf", str(p))

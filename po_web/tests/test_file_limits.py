# -*- coding: utf-8 -*-
"""Quotations arrive from outside the company: a small file must never be able to exhaust the server's memory (audit M-14)."""
import zipfile

import pytest

import quote2po as q
import quote_read

S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _xlsx(path, sheet_xml):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/workbook.xml", '<workbook xmlns="%s" xmlns:r="%s"><sheets><sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>' % (S, R))
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def test_a_compression_bomb_is_refused_before_anything_is_unpacked(tmp_path):
    f = tmp_path / "offer.xlsx"
    with zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/workbook.xml", b"0" * (150 * 1024 * 1024))                  # 150 MB of zeros: a few hundred KB on disk
    assert f.stat().st_size < 1024 * 1024
    with pytest.raises(q.UnsafeFile):
        q.convert_to_md(str(f))


def test_the_reader_reports_a_refused_file_instead_of_crashing(tmp_path):
    f = tmp_path / "offer.xlsx"
    with zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/workbook.xml", b"0" * (150 * 1024 * 1024))
    r = quote_read.read_offer(str(f))
    assert r["items"] == [] and "لم يُقرأ الملف" in r["notes"][0]


def test_too_many_parts_are_refused(tmp_path):
    f = tmp_path / "offer.xlsx"
    with zipfile.ZipFile(f, "w") as z:
        for i in range(q.MAX_MEMBERS + 5):
            z.writestr("p%d.txt" % i, b"x")
    with pytest.raises(q.UnsafeFile):
        q.check_zip_safe(str(f))


def test_far_away_cells_and_endless_rows_are_cut_not_expanded(tmp_path):
    rows = "".join('<row r="%d"><c r="A%d" t="inlineStr"><is><t>item %d</t></is></c></row>' % (i, i, i) for i in range(1, q.MAX_SHEET_ROWS + 3000))
    far = '<row r="1"><c r="A1" t="inlineStr"><is><t>x</t></is></c><c r="XFD1" t="inlineStr"><is><t>far</t></is></c></row>'
    f = tmp_path / "ok.xlsx"
    _xlsx(f, '<worksheet xmlns="%s"><sheetData>%s%s</sheetData></worksheet>' % (S, far, rows))
    md = q.xlsx_to_md(str(f))
    assert "far" not in md                                                         # the cell at column XFD was ignored
    assert "item %d" % (q.MAX_SHEET_ROWS + 2000) not in md and "item 100" in md      # rows past the limit were cut


def test_a_normal_xlsx_still_reads(tmp_path):
    f = tmp_path / "ok.xlsx"
    _xlsx(f, '<worksheet xmlns="%s"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Cement</t></is></c><c r="B1"><v>12</v></c></row></sheetData></worksheet>' % S)
    assert "Cement" in q.convert_to_md(str(f))[0]

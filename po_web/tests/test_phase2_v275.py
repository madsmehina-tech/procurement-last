# -*- coding: utf-8 -*-
"""v27.5 - reliability: edit conflicts, audit outcomes, real file types, page links on long lists."""
import io
import re
from datetime import datetime, timedelta

import pytest

import conftest
import fake_db


@pytest.fixture(autouse=True)
def _schema():
    conftest.build_schema()


@pytest.fixture()
def client():
    import app as appmod
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    return c


def _order(po="PO-7"):
    fake_db.execute("INSERT INTO [PurchaseOrders] ([PONumber], [SupplierName], [Project], [OrderDate], [TotalAmount], [CreatedAt], [ModifiedAt]) VALUES (?, 'مورد', 'مشروع', ?, 115, ?, ?)",
                    (po, datetime.now(), datetime.now() - timedelta(days=1), datetime.now() - timedelta(days=1)))
    fake_db.execute("INSERT INTO [OrderItems] ([PONumber], [LineNo], [ItemName], [Unit], [Qty], [UnitPrice], [QtyReceived]) VALUES (?, 1, 'حديد', 'طن', 1, 100, 0)", (po,))


def _edit_form(version, price="200"):
    return {"po_number": "PO-7", "supplier": "مورد", "project": "مشروع", "order_date": "2026-11-01", "discount": "0", "loaded_version": version,
            "item_name": ["حديد"], "item_unit": ["طن"], "item_qty": ["1"], "item_price": [price]}


@pytest.fixture()
def no_files(monkeypatch):
    """The edit route writes safety copies into the archive and builds PDFs: replace those parts (they have their own tests)."""
    import archive
    import po_files
    monkeypatch.setattr(po_files, "archive_snapshot", lambda *a, **k: {"RelPath": "x.json", "DocID": 0})
    monkeypatch.setattr(po_files, "archive_previous_pdf", lambda *a, **k: {"DocID": 0})
    monkeypatch.setattr(po_files, "save_current", lambda *a, **k: {"pdf": True})
    monkeypatch.setattr(archive, "discard_document", lambda *a, **k: None)


def _price():
    return fake_db.query("SELECT [UnitPrice] FROM [OrderItems] WHERE [PONumber] = 'PO-7'")[0]["UnitPrice"]


# ---------------------------------------------------------------- an edit made on an out-of-date page is NOT saved silently
def test_an_edit_on_an_out_of_date_page_is_refused_then_accepted_on_the_second_deliberate_save(client, no_files):
    _order()
    page = client.get("/orders/PO-7/edit").get_data(as_text=True)
    opened = re.search(r'name="loaded_version" value="([^"]*)"', page).group(1)
    # a colleague saves first
    fake_db.execute("UPDATE [PurchaseOrders] SET [RowVersion] = 1, [ModifiedAt] = ? WHERE [PONumber] = 'PO-7'", (datetime.now(),))
    r = client.post("/orders/PO-7/edit", data=_edit_form(opened, "200"))
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "تعارض" in html
    assert _price() == 100                                              # nothing was overwritten
    fresh = re.search(r'name="loaded_version" value="([^"]*)"', html).group(1)
    assert fresh != opened
    r2 = client.post("/orders/PO-7/edit", data=_edit_form(fresh, "200"))   # the person reviewed and saves again on purpose
    assert r2.status_code == 302 and _price() == 200


def test_an_edit_on_a_current_page_saves_normally(client, no_files):
    _order()
    page = client.get("/orders/PO-7/edit").get_data(as_text=True)
    v = re.search(r'name="loaded_version" value="([^"]*)"', page).group(1)
    assert client.post("/orders/PO-7/edit", data=_edit_form(v, "150")).status_code == 302
    assert _price() == 150


# ---------------------------------------------------------------- the audit says what happened, not just that a POST was answered
def _audit(action):
    return [r["Details"] for r in fake_db.query("SELECT * FROM [AuditLog] WHERE [ActionName] = ?", (action,))]


def test_a_rejected_form_is_logged_as_rejected_and_a_good_one_as_done(client):
    bad = {"po_number": "PO-BAD", "supplier": "", "project": "", "item_name": ["x"], "item_unit": ["u"], "item_qty": ["1"], "item_price": ["-5"], "discount": "0"}
    assert client.post("/orders/new", data=bad).status_code == 200      # re-shown with errors (HTTP 200!)
    assert any("لم تُحفظ" in d for d in _audit("order_new"))
    _order("PO-8")
    client.post("/orders/PO-8/status", data={"kind": "receipt", "value": "مكتمل", "note": ""})
    d = _audit("update_status")
    assert d and "نجحت" in d[-1] and "receipt" in d[-1] and "←" in d[-1]    # outcome AND before ← after


def test_a_refused_business_rule_behind_a_redirect_is_logged_as_refused(client):
    client.post("/orders/PO-NONE/status", data={"kind": "bogus", "value": "x"})       # «طلب غير صالح» + redirect
    d = _audit("update_status")
    assert d and "رُفضت" in d[-1]


# ---------------------------------------------------------------- the real content must match the extension
def test_a_program_renamed_to_pdf_is_refused_but_real_files_pass():
    import archive
    real = {"pdf": b"%PDF-1.7 ...\n%%EOF\n", "png": b"\x89PNG\r\n\x1a\n....\x00\x00\x00\x00IEND\xaeB`\x82", "jpg": b"\xff\xd8\xff\xe0....\xff\xd9"}
    for ext, good in real.items():
        archive.check_content(ext, good)
        with pytest.raises(archive.MismatchedFile):
            archive.check_content(ext, b"MZ\x90\x00 this is a program")
    archive.check_content("pdf", b"\n\n junk before %PDF-1.4 is allowed by readers ... %%EOF")     # a few bytes before the header are legal
    archive.check_content("txt", b"anything")                                                      # unknown types are not this check's business


def test_the_upload_gate_uses_it(monkeypatch, tmp_path):
    import archive
    monkeypatch.setattr(archive.config, "ARCHIVE_ROOT", str(tmp_path))
    with pytest.raises(archive.MismatchedFile):
        archive.store_document("PO", "PO-1", "x", "عرض سعر", b"MZ not a pdf", "evil.pdf")


# ---------------------------------------------------------------- long lists are split into pages, totals stay complete
def test_the_orders_list_has_pages_and_keeps_the_totals_of_the_whole_list(client):
    for i in range(250):
        fake_db.execute("INSERT INTO [PurchaseOrders] ([PONumber], [SupplierName], [Project], [OrderDate], [TotalAmount], [CreatedAt]) VALUES (?, 'م', 'p', ?, 10, ?)",
                        ("PO-%04d" % i, datetime.now(), datetime.now()))
    p1 = client.get("/orders").get_data(as_text=True)
    assert len(re.findall(r"<tr>\s*<td>\d+</td>", p1)) == 100 and 'class="pager"' in p1
    assert "250" in p1                                                  # the count of ALL orders is still shown
    p3 = client.get("/orders?page=3").get_data(as_text=True)
    assert len(re.findall(r"<tr>\s*<td>\d+</td>", p3)) == 50
    assert client.get("/orders?page=999").status_code == 200            # out-of-range page = the last page, not an error
    assert client.get("/orders?page=abc").status_code == 200

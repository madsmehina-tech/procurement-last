# -*- coding: utf-8 -*-
"""v27.4 - the defects the second outside review proved. Each test fails on the v27.3 code."""
import importlib
import os
import re
import sqlite3
from datetime import datetime

import pytest

import conftest
import fake_db
import helpers as h


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


def _po(n, supplier="مورد"):
    fake_db.execute("INSERT INTO [PurchaseOrders] ([PONumber], [SupplierName], [Project], [OrderDate], [TotalAmount]) VALUES (?, ?, 'p', ?, 10)",
                    (n, supplier, datetime.now()))


# ---------------------------------------------------------------- A: a skipped unique index is never recorded as done
def test_a_skipped_index_makes_the_migration_incomplete_and_a_later_run_completes_it(capsys):
    m26 = importlib.import_module("migrations.m026_unique_numbers")
    m32 = importlib.import_module("migrations.m032_ensure_unique_indexes")
    fake_db.execute("DROP INDEX ux_po_number")
    _po("PO-9")
    _po("PO-9")
    assert m26.main() is False                                  # incomplete: the runner must NOT record it
    assert [n for _t, n, _l in m26.missing()] == ["ux_po_number"]
    fake_db.execute("DELETE FROM [PurchaseOrders] WHERE [PONumber] = 'PO-9'")
    _po("PO-9")
    assert m32.main() is True                                   # the repair migration finishes the job
    assert m26.missing() == []
    with pytest.raises(sqlite3.IntegrityError):
        _po("PO-9")


def test_the_runner_does_not_record_an_incomplete_migration(capsys):
    from migrations import runner
    runner.ensure_version_table()
    for n in runner.discover():
        if n != "m032_ensure_unique_indexes":
            fake_db.execute("INSERT INTO [SchemaVersion] ([Version], [Description], [AppliedAt]) VALUES (?, '', ?)", (n, datetime.now()))
    fake_db.execute("DROP INDEX ux_po_number")
    _po("PO-1")
    _po("PO-1")
    ok = runner.run(backup=False)
    assert ok is False
    assert "m032_ensure_unique_indexes" not in runner.applied()
    assert "لم يكتمل" in capsys.readouterr().out


# ---------------------------------------------------------------- B: an order number with «/» opens, edits and prints
@pytest.mark.parametrize("number", ["PO/2026/001", "A~B/7", "PO-2026-002"])
def test_order_numbers_with_a_slash_or_tilde_work_on_every_page(client, number):
    _po(number)
    from flask import url_for
    import app as appmod
    with appmod.app.test_request_context():
        detail, edit = url_for("order_detail", po_number=number), url_for("order_edit", po_number=number)
    assert client.get(detail).status_code == 200
    assert client.get(edit).status_code == 200
    assert number in client.get(detail).get_data(as_text=True)


# ---------------------------------------------------------------- C: an absurd amount is a polite refusal, never a 500
def test_a_very_long_amount_is_refused_politely(client):
    import money_guards as mg
    with pytest.raises(mg.MoneyError):
        mg.parse_money("1" * 60)
    form = {"po_number": "PO-LONG-1", "supplier": "م", "project": "p", "item_name": ["x"], "item_unit": ["u"], "item_qty": ["1"], "item_price": ["1" * 60], "discount": "0"}
    r = client.post("/orders/new", data=form)
    assert r.status_code == 200 and "كبير جدًا" in r.get_data(as_text=True)


# ---------------------------------------------------------------- D: no transaction, no save (see test_security_v273 for every driver code)
def test_there_is_no_fallback_branch_left_in_db_py():
    src = open(os.path.join(conftest.ROOT, "db.py"), encoding="utf-8").read()
    body = src[src.index("def transaction()"):src.index("# ---- text longer than its column")]
    code = re.sub(r'""".*?"""', "", body, flags=re.S)           # the docstring may talk about it; the code must not
    assert "IM001" not in code and "get_conn()" not in code and "real" not in code


# ---------------------------------------------------------------- E: text typed by a user never becomes an Excel formula
def test_text_starting_with_equals_is_stored_as_text_in_excel():
    import xlsx_safe
    from openpyxl import Workbook
    ws = Workbook().active
    ws["A1"] = '=HYPERLINK("http://evil","click")'
    ws.append(["=1+1", "ok"])
    assert ws["A1"].data_type == "s" and ws["A2"].data_type == "s" and ws["B2"].data_type == "s"
    with xlsx_safe.allow_formulas():
        ws["C1"] = "=SUM(1,2)"
    assert ws["C1"].data_type == "f"
    ws["D1"] = "=SUM(1,2)"
    assert ws["D1"].data_type == "s"                           # the permission ended with the `with` block


def test_a_real_export_keeps_a_hostile_supplier_name_as_text(client):
    import io
    from openpyxl import load_workbook
    fake_db.execute("INSERT INTO [Suppliers] ([Name]) VALUES (?)", ('=HYPERLINK("http://evil","x")',))
    r = client.get("/suppliers/export.xlsx")
    assert r.status_code == 200
    seen = 0
    for ws in load_workbook(io.BytesIO(r.data)):
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, str) and "HYPERLINK" in c.value:
                    seen += 1
                    assert c.data_type != "f"
    assert seen >= 1


# ---------------------------------------------------------------- F: the exposed session key is replaced once
def test_the_session_key_is_replaced_on_the_first_start_of_this_version(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "BASE_DIR", str(tmp_path))
    monkeypatch.delenv("PO_SECRET_KEY", raising=False)
    inst = tmp_path / "instance"
    inst.mkdir()
    (inst / "secret.key").write_text("OLD-EXPOSED-KEY", encoding="ascii")
    first = config.secret_key()
    assert first != "OLD-EXPOSED-KEY" and len(first) == 64
    assert (inst / "key_rotated_v27_4").exists()
    assert config.secret_key() == first                       # afterwards it is stable (no daily logouts)


# ---------------------------------------------------------------- G: labels are tied to their fields in plain HTML
def test_login_and_register_fields_are_labelled_without_javascript(client):
    for page in ("/login", "/register"):
        html = client.get(page).get_data(as_text=True)
        for m in re.finditer(r"<input [^>]*>", html):
            tag = m.group(0)
            if 'type="hidden"' in tag or 'type="submit"' in tag:
                continue
            fid = re.search(r'id="([^"]+)"', tag)
            assert fid and ('for="%s"' % fid.group(1)) in html, tag

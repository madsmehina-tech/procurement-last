# -*- coding: utf-8 -*-
"""A signed contract is a document: its text, number and money never change by accident (audit C-11 / A-01, A-13)."""
from datetime import datetime
from io import BytesIO

import pytest

import conftest
import contract_gen as cg
import fake_db
import helpers as h


@pytest.fixture()
def world():
    conftest.build_schema()
    return h.add_sub()


@pytest.fixture()
def client(world):
    import app as appmod
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = 1
    return c


def _contract(sub, status="ساري", origin="مولَّد", **extra):
    cols = {"SubcontractorID": sub, "Origin": origin, "Status": status, "ContractNo": "SC-2026-001", "ContractValue": 100000.0, "Revision": 1, "CreatedAt": datetime.now()}
    cols.update(extra)
    return fake_db.execute_insert("INSERT INTO [Contracts] (%s) VALUES (%s)" % (", ".join("[%s]" % c for c in cols), ", ".join("?" for _ in cols)), tuple(cols.values()))


def _edit(client, cid, **over):
    data = {"contract_no": "SC-2026-001", "status": "ساري", "contract_value": "100000", "notes": "x"}
    data.update(over)
    return client.post("/contracts/%d/edit" % cid, data=data)


@pytest.mark.parametrize("status", ["ساري", "موقوف", "منتهي", "مفسوخ"])
def test_a_signed_contract_cannot_be_saved_from_the_wizard(world, status):
    cid = _contract(world, status)
    with pytest.raises(cg.ContractError):
        cg.save({}, "tester", cid)


def test_a_signed_contract_cannot_be_regenerated(world):
    cid = _contract(world, "ساري")
    with pytest.raises(cg.ContractError):
        cg.generate(cid, "tester")


def test_the_value_and_number_of_a_generated_contract_do_not_change_from_the_edit_form(client, world):
    cid = _contract(world, "ساري")
    _edit(client, cid, contract_value="1")
    _edit(client, cid, contract_no="HACK-1")
    row = fake_db.query("SELECT * FROM [Contracts] WHERE [ContractID] = ?", (cid,))[0]
    assert row["ContractValue"] == 100000.0 and row["ContractNo"] == "SC-2026-001"


def test_an_archived_contract_keeps_its_old_freedom(client, world):
    cid = _contract(world, "ساري", origin="مؤرشف")
    _edit(client, cid, contract_value="250000")
    assert fake_db.scalar("SELECT [ContractValue] FROM [Contracts] WHERE [ContractID] = ?", (cid,)) == 250000.0


def test_a_signed_contract_cannot_go_back_to_a_draft(client, world):
    cid = _contract(world, "ساري")
    _edit(client, cid, status="مسودة")
    assert fake_db.scalar("SELECT [Status] FROM [Contracts] WHERE [ContractID] = ?", (cid,)) == "ساري"


def test_a_contract_cannot_be_made_active_by_hand_without_its_signed_copy(client, world):
    cid = _contract(world, "صادر")
    _edit(client, cid, status="ساري")
    assert fake_db.scalar("SELECT [Status] FROM [Contracts] WHERE [ContractID] = ?", (cid,)) == "صادر"
    fake_db.execute("INSERT INTO [Documents] ([EntityType], [EntityKey], [DocType], [IsCurrent], [UploadedAt]) VALUES ('CONTRACT', ?, 'العقد الموقّع', 1, ?)", (str(cid), datetime.now()))
    _edit(client, cid, status="ساري")
    assert fake_db.scalar("SELECT [Status] FROM [Contracts] WHERE [ContractID] = ?", (cid,)) == "ساري"


def test_only_a_draft_generated_contract_can_be_deleted_and_nothing_is_left_behind(client, world):
    signed = _contract(world, "ساري")
    client.post("/contracts/%d/delete" % signed)
    assert fake_db.scalar("SELECT COUNT(*) FROM [Contracts] WHERE [ContractID] = ?", (signed,)) == 1
    draft = _contract(world, "مسودة", ContractNo="SC-2026-002")
    fake_db.execute("INSERT INTO [ContractData] ([ContractID], [FieldKey], [FieldValue]) VALUES (?, 'x', 'y')", (draft,))
    fake_db.execute("INSERT INTO [ContractHistory] ([ContractID], [At], [Action]) VALUES (?, ?, 'x')", (draft, datetime.now()))
    client.post("/contracts/%d/delete" % draft)
    assert fake_db.scalar("SELECT COUNT(*) FROM [Contracts] WHERE [ContractID] = ?", (draft,)) == 0
    assert fake_db.scalar("SELECT COUNT(*) FROM [ContractData] WHERE [ContractID] = ?", (draft,)) == 0
    assert fake_db.scalar("SELECT COUNT(*) FROM [ContractHistory] WHERE [ContractID] = ?", (draft,)) == 0


class _F:
    def __init__(self, name):
        self.filename = name

    def read(self):
        return b"x"

    def save(self, p):
        open(p, "wb").write(b"x")


def test_the_signed_copy_must_be_a_pdf_or_image_and_the_contract_must_have_been_generated(world):
    cid = _contract(world, "صادر")
    with pytest.raises(cg.ContractError):
        cg.upload_signed(cid, _F("sheet.xlsx"), "tester")
    assert fake_db.scalar("SELECT [Status] FROM [Contracts] WHERE [ContractID] = ?", (cid,)) == "صادر"
    never = _contract(world, "مسودة", ContractNo="SC-2026-009", Revision=0)
    with pytest.raises(cg.ContractError):
        cg.upload_signed(never, _F("signed.pdf"), "tester")

# -*- coding: utf-8 -*-
"""Restoring an OLD backup and running the migrations must work (audit M-15): m019 used to read columns that only exist after m025."""
from datetime import datetime

import conftest
import fake_db


def test_contract_folder_works_on_a_database_older_than_m025():
    conftest.build_schema()
    fake_db.execute("ALTER TABLE [Contracts] DROP COLUMN [PartyKind]")
    fake_db.execute("ALTER TABLE [Contracts] DROP COLUMN [SupplierID]")
    sub = fake_db.execute_insert("INSERT INTO [Subcontractors] ([Name], [CreatedAt]) VALUES ('مقاول قديم', ?)", (datetime.now(),))
    cid = fake_db.execute_insert("INSERT INTO [Contracts] ([SubcontractorID], [ContractNo], [Status], [CreatedAt]) VALUES (?, 'C-1', 'ساري', ?)", (sub, datetime.now()))
    import archive
    folder = archive.ensure_contract_folder(cid)
    assert folder and "مقاول قديم" in folder

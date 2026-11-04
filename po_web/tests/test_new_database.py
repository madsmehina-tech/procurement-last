# -*- coding: utf-8 -*-
"""v27.9 - `python migrate.py --new-database` builds the whole schema in a brand-new EMPTY database (found by running the installer on a real Windows machine:
migrate.py used to stop with «PurchaseOrders not found»)."""
import conftest
import fake_db


def test_an_empty_database_is_refused_without_the_flag_and_built_with_it(monkeypatch, capsys):
    from migrations import runner
    fake_db.reset()
    assert runner.run(backup=False) is False
    assert "--new-database" in capsys.readouterr().out and not fake_db.table_exists("PurchaseOrders")
    monkeypatch.setenv("PO_ADMIN_USER", "boss")
    monkeypatch.setenv("PO_ADMIN_PASSWORD", "Passw0rd-1")
    assert runner.run(backup=False, new_db=True) is True
    for t in ("PurchaseOrders", "OrderItems", "Suppliers", "Users", "Documents", "Certificates", "PaymentVouchers"):
        assert fake_db.table_exists(t), t
    assert fake_db.query("SELECT [Username] FROM [Users]")[0]["Username"] == "boss"
    assert runner.pending() == [] and "m033_order_row_version" in runner.applied()
    assert runner.run(backup=False, new_db=True) is True                                       # running it again is harmless


def test_a_wrong_database_is_still_refused_even_with_other_tables(monkeypatch):
    from migrations import runner
    fake_db.reset()
    fake_db.execute("CREATE TABLE [SomethingElse] ([A] INTEGER)")
    assert runner.run(backup=False) is False and not fake_db.table_exists("PurchaseOrders")

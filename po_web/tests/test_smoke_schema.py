import conftest


def test_all_migrations_build_schema():
    conftest.build_schema()
    import fake_db
    for t in ("Certificates", "Payments", "PaymentCycles", "CycleDecisions", "LedgerEntries", "Contracts", "Users", "Documents"):
        assert fake_db.table_exists(t), t

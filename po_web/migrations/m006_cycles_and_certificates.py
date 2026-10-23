# -*- coding: utf-8 -*-
"""
extend_cycles.py - run this ONCE (python extend_cycles.py) to bring the
database up to the new "دورة الصرف" (payment cycle) design:

- Contracts: adds ContractValue / RetentionPct / AdvancePct (reference-only
  fields, not used in any automatic calculation).
- NEW ContractSites: one contract can now cover several sites (reverts the
  earlier "one contract per site" design). Existing Contracts.SiteName values
  are copied into this table automatically so nothing is lost; the column
  itself is left in place, unused going forward, rather than dropped.
- Certificates: adds SuppliedMaterials / Penalties (split out of the old
  single "Deductions" field - existing Deductions values are copied into
  SuppliedMaterials so old certificates keep their net amount unchanged),
  ReviewedByAccountant / ReviewedByManager / RejectionReason, and CycleID
  (set once a certificate is placed in a payment cycle).
- NEW PaymentCycles and CycleDecisions: the payment-cycle workflow itself.
- Suppliers: adds CRFile / TaxCertFile (CatalogFile and QualificationFile
  already exist from the earlier supplier-qualification work).
- PurchaseOrders: adds a date and a note for each of the three statuses
  (receipt, invoice, payment).

Safe to run again later: every step only adds what is not already there.
"""
import db

NEW_COLUMNS = [
    ("Contracts", [
        ("ContractValue", "DOUBLE"),
        ("RetentionPct", "DOUBLE"),
        ("AdvancePct", "DOUBLE"),
    ]),
    ("Certificates", [
        ("SuppliedMaterials", "DOUBLE"),
        ("Penalties", "DOUBLE"),
        ("ReviewedByAccountant", "YESNO"),
        ("ReviewedByManager", "YESNO"),
        ("RejectionReason", "MEMO"),
        ("CycleID", "INTEGER"),
    ]),
    ("Suppliers", [
        ("CRFile", "TEXT(255)"),
        ("TaxCertFile", "TEXT(255)"),
    ]),
    ("PurchaseOrders", [
        ("ReceiptDate", "DATETIME"),
        ("ReceiptNote", "MEMO"),
        ("InvoiceNote", "MEMO"),
        ("PaymentNote", "MEMO"),
    ]),
]

NEW_TABLES = [
    ("ContractSites",
     "CREATE TABLE [ContractSites] ("
     "[SiteID] AUTOINCREMENT PRIMARY KEY, "
     "[ContractID] INTEGER, [SiteName] TEXT(255), [CreatedAt] DATETIME)"),
    ("PaymentCycles",
     "CREATE TABLE [PaymentCycles] ("
     "[CycleID] AUTOINCREMENT PRIMARY KEY, "
     "[CycleNo] INTEGER, [Status] TEXT(30), [LiquidityCap] DOUBLE, "
     "[CreatedAt] DATETIME, [ApprovedAt] DATETIME, [ApprovedBy] TEXT(255))"),
    ("CycleDecisions",
     "CREATE TABLE [CycleDecisions] ("
     "[DecisionID] AUTOINCREMENT PRIMARY KEY, "
     "[CycleID] INTEGER, [CertificateID] INTEGER, [DecisionType] TEXT(20), "
     "[PartialAmount] DOUBLE, [ManagementNote] MEMO)"),
]


def existing_columns(table):
    conn = db.get_conn()
    try:
        cur = conn.cursor()
        return {row.column_name.lower() for row in cur.columns(table=table)}
    finally:
        conn.close()


def add_columns():
    for table, columns in NEW_COLUMNS:
        if not db.table_exists(table):
            print(f"! {table} does not exist - run the earlier setup scripts first.")
            continue
        have = existing_columns(table)
        for name, coltype in columns:
            if name.lower() in have:
                print(f"- {table}.{name}: already exists, unchanged")
                continue
            db.execute(f"ALTER TABLE [{table}] ADD COLUMN [{name}] {coltype}")
            print(f"- {table}.{name}: added")


def add_tables():
    for name, sql in NEW_TABLES:
        if db.table_exists(name):
            print(f"- {name}: already exists, unchanged")
            continue
        db.execute(sql)
        print(f"- {name}: created")


def backfill_sites():
    """Copies every Contract's current SiteName into ContractSites, once -
    skips a contract that already has a matching site row (safe to re-run)."""
    contracts = db.query("SELECT [ContractID], [SiteName] FROM [Contracts]")
    moved = 0
    for c in contracts:
        site = (c["SiteName"] or "").strip()
        if not site:
            continue
        existing = db.query(
            "SELECT [SiteID] FROM [ContractSites] WHERE [ContractID] = ? AND [SiteName] = ?",
            (c["ContractID"], site),
        )
        if existing:
            continue
        db.execute(
            "INSERT INTO [ContractSites] (ContractID, SiteName, CreatedAt) VALUES (?, ?, Now())",
            (c["ContractID"], site),
        )
        moved += 1
    print(f"- ContractSites backfill: {moved} site(s) copied from existing contracts")


def backfill_deductions():
    """Copies each certificate's old single Deductions value into the new
    SuppliedMaterials field (Penalties stays 0) so existing net amounts are
    unchanged - only touches rows where SuppliedMaterials is still empty."""
    certs = db.query(
        "SELECT [CertificateID], [Deductions] FROM [Certificates] "
        "WHERE [SuppliedMaterials] IS NULL"
    )
    for c in certs:
        db.execute(
            "UPDATE [Certificates] SET [SuppliedMaterials] = ?, [Penalties] = 0 WHERE [CertificateID] = ?",
            (c["Deductions"] or 0, c["CertificateID"]),
        )
    print(f"- Certificates.SuppliedMaterials backfill: {len(certs)} row(s) copied from Deductions")


def main():
    add_columns()
    add_tables()
    backfill_sites()
    backfill_deductions()
    print()
    print("Done.")


if __name__ == "__main__":
    main()

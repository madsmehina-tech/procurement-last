# -*- coding: utf-8 -*-
"""
extend_subcontractors.py - run this ONCE (python extend_subcontractors.py)
to add all the new columns requested for the subcontractor module:
separate CR/tax/bank certificate uploads, a signed-contract upload, and
the richer certificate (مستخلص) fields (deductions, VAT, the certificate
document itself, and the payment proof).

Safe to run again later: only adds a column if it is not already there.
"""
import db

CHANGES = [
    ("Subcontractors", [
        ("CRFile", "TEXT(255)"),
        ("TaxCertFile", "TEXT(255)"),
        ("BankCertFile", "TEXT(255)"),
    ]),
    ("Contracts", [
        ("ContractFile", "TEXT(255)"),
    ]),
    ("Certificates", [
        ("Deductions", "CURRENCY"),
        ("CertificateFile", "TEXT(255)"),
        ("PaymentProofFile", "TEXT(255)"),
    ]),
]


def existing_columns(table):
    conn = db.get_conn()
    try:
        cur = conn.cursor()
        return {row.column_name.lower() for row in cur.columns(table=table)}
    finally:
        conn.close()


def main():
    for table, columns in CHANGES:
        if not db.table_exists(table):
            print(f"! {table} does not exist - run the earlier setup script first.")
            continue
        have = existing_columns(table)
        for name, coltype in columns:
            if name.lower() in have:
                print(f"- {table}.{name}: already exists, unchanged")
                continue
            db.execute(f"ALTER TABLE [{table}] ADD COLUMN [{name}] {coltype}")
            print(f"- {table}.{name}: added")
    print()
    print("Done.")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
create_subcontractors.py - run this ONCE (python create_subcontractors.py)
to add the three subcontractor-tracking tables to PurchaseSystem.accdb.

Safe to run again later: skips any table that already exists, exactly
like create_database.vbs did on the Excel side.
"""
import db

TABLES = [
    ("Subcontractors",
     "CREATE TABLE [Subcontractors] ("
     "[SubcontractorID] AUTOINCREMENT PRIMARY KEY, "
     "[Name] TEXT(255), [CR] TEXT(50), [TaxNumber] TEXT(50), "
     "[BankAccount] TEXT(100), [Phone] TEXT(50), "
     "[QualificationFile] TEXT(255), [QualificationUpdatedAt] DATETIME, "
     "[Notes] MEMO, [CreatedAt] DATETIME)"),
    ("Contracts",
     "CREATE TABLE [Contracts] ("
     "[ContractID] AUTOINCREMENT PRIMARY KEY, "
     "[SubcontractorID] INTEGER, [Project] TEXT(255), "
     "[ContractValue] CURRENCY, [RetentionPercent] DOUBLE, "
     "[StartDate] DATETIME, [EndDate] DATETIME, "
     "[Status] TEXT(20), [CreatedAt] DATETIME)"),
    ("Certificates",
     "CREATE TABLE [Certificates] ("
     "[CertificateID] AUTOINCREMENT PRIMARY KEY, "
     "[ContractID] INTEGER, [SiteName] TEXT(255), [CertificateNo] INTEGER, "
     "[CertificateDate] DATETIME, [GrossAmount] CURRENCY, "
     "[Status] TEXT(20), [PaymentDate] DATETIME, [Notes] MEMO, "
     "[CreatedAt] DATETIME)"),
]


def main():
    for name, sql in TABLES:
        if db.table_exists(name):
            print(f"- {name}: already exists, unchanged")
            continue
        db.execute(sql)
        print(f"- {name}: created")
    print()
    print("Done.")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
r"""
m022 - the contractor's account statement (كشف الحساب) and the «الدفعات» section of the contractor's page.

1. LedgerEntries: the movements typed by hand on the contractor's page.
     مدين  = an entitlement of the contractor  (رد خصم، رد ضريبة، استحقاق آخر)      - certificates are debits too, read from Certificates
     دائن  = a payment or a charge against him (دفعة مباشرة، مواد على المقاول، تسوية) - cycle transfers are credits too, read from Payments
   A direct payment may be recorded before it is executed (Status «مسجّلة»): it can enter a later payment cycle. Only
   executed movements («منفّذة») appear in the statement.
2. Payments.Statement: the wording of the payment («البيان»), decided by the real case when the transfer is confirmed.
3. CycleDecisions.EntryID: a line of a payment cycle can be a direct payment that was recorded and not executed yet.
Nothing existing is changed or removed. Safe to run again.
"""
import db
from migrations._util import add_column

DESCRIPTION = "كشف حساب المقاول والدفعات المباشرة"


def main():
    if not db.table_exists("LedgerEntries"):
        db.execute("CREATE TABLE [LedgerEntries] ([EntryID] AUTOINCREMENT PRIMARY KEY, [SubcontractorID] INTEGER, [Kind] TEXT(10), [Category] TEXT(40), "
                   "[Description] TEXT(250), [Amount] CURRENCY, [EntryDate] DATETIME, [ProjectID] INTEGER, [SiteID] INTEGER, [ProjectName] TEXT(150), "
                   "[SiteName] TEXT(150), [Status] TEXT(20), [CycleID] INTEGER, [ExecAt] DATETIME, [BankRef] TEXT(80), [Note] TEXT(250), "
                   "[CreatedBy] TEXT(50), [CreatedAt] DATETIME)")
        print("- LedgerEntries: created")
    else:
        print("- LedgerEntries: already exists, unchanged")
    add_column("Payments", "Statement", "TEXT(250)")
    add_column("CycleDecisions", "EntryID", "INTEGER")


if __name__ == "__main__":
    main()

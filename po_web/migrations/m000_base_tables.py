# -*- coding: utf-8 -*-
"""
m000_base_tables - the three tables that were created by hand, before the numbered migrations existed:
Suppliers, PurchaseOrders, OrderItems.

Why it exists: every later migration ADDS columns to these tables, but nothing created them, so a brand-new database (a new server, a new PC, a
disaster-recovery rebuild) could not be built from the project alone. On an existing database this migration finds the tables already there and
changes NOTHING. The columns here are the ones the first version of the system used; later migrations (m002, m007, m009, m010 ...) add the rest.
"""
import db

TABLES = [
    ("Suppliers",
     "CREATE TABLE [Suppliers] ([SupplierID] AUTOINCREMENT PRIMARY KEY, [Name] TEXT(255), [CR] TEXT(50), [TaxNumber] TEXT(50), "
     "[PaymentTermsDays] INTEGER, [Phone] TEXT(50), [CreatedAt] DATETIME)"),
    ("PurchaseOrders",
     "CREATE TABLE [PurchaseOrders] ([PONumber] TEXT(50), [OrderDate] DATETIME, [HijriDate] TEXT(50), [SupplierName] TEXT(255), "
     "[QuoteNo] TEXT(100), [Project] TEXT(255), [Receiver] TEXT(255), [ReceiverPhone] TEXT(50), [TotalAmount] CURRENCY, "
     "[ReceiptStatus] TEXT(30), [InvoiceStatus] TEXT(30), [PaymentStatus] TEXT(30), [InvoiceDate] DATETIME, [InvoiceDueDate] DATETIME, "
     "[PaymentDate] DATETIME, [CreatedAt] DATETIME, [ModifiedAt] DATETIME)"),
    ("OrderItems",
     "CREATE TABLE [OrderItems] ([PONumber] TEXT(50), [LineNo] INTEGER, [ItemName] TEXT(255), [Unit] TEXT(50), [Qty] DOUBLE, "
     "[UnitPrice] CURRENCY, [QtyReceived] DOUBLE)"),
]


def main():
    for name, sql in TABLES:
        if db.table_exists(name):
            print("- %s: already exists, unchanged" % name)
            continue
        db.execute(sql)
        print("- %s: created" % name)

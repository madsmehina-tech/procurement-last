# -*- coding: utf-8 -*-
"""
m035_v29_look_print_rating_alerts - what version 29.0 stores:
 * CompanyProfile    ThemeJson (the look of the system), PrintJson (the print settings of each document), RatingJson (the weights of the
                     supplier rating), AlertsJson (each alert: on/off, its days, the roles and the people it goes to). Empty = the defaults.
 * PurchaseOrders    AgreedDeliveryDate (the delivery date agreed with the supplier: required on a NEW order), ReceiptQuality (سليم /
                     بملاحظات / مرفوض جزئياً, chosen when the goods are received). Old orders stay empty: the rating uses what exists.
 * Contracts         RetentionDueDate (when the retention of a subcontract is due back: the alert «استرداد المحتجز» reads it)
 * NotificationReads which alert each person has already seen (UserID + the alert's key)
Nothing existing is changed.
"""
import db
from migrations._util import add_column

DESCRIPTION = "المظهر وإعدادات الطباعة وتقييم الموردين والتنبيهات"

TABLES = {
    "NotificationReads": "CREATE TABLE [NotificationReads] ([ReadID] AUTOINCREMENT PRIMARY KEY, [UserID] INTEGER, [AlertKey] TEXT(200), [ReadAt] DATETIME)",
}


def main():
    if db.table_exists("CompanyProfile"):
        for col in ("ThemeJson", "PrintJson", "RatingJson", "AlertsJson"):
            add_column("CompanyProfile", col, "MEMO")
    if db.table_exists("PurchaseOrders"):
        add_column("PurchaseOrders", "AgreedDeliveryDate", "DATETIME")
        add_column("PurchaseOrders", "ReceiptQuality", "TEXT(30)")
    if db.table_exists("Contracts"):
        add_column("Contracts", "RetentionDueDate", "DATETIME")
    for name, sql in TABLES.items():
        if not db.table_exists(name):
            db.execute(sql)
            print("- %s: created" % name)
    return True

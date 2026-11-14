# -*- coding: utf-8 -*-
"""
m034_v28_currency_signatures - what version 28.0 stores:
 * PurchaseOrders   VatMode (add | incl | none), VatPct, CurrencyCode, ExchangeRate, TotalCurrency (the total in the order's own currency;
                    TotalAmount stays the equivalent in riyals, so every existing sum and report keeps adding riyals)
 * PaymentVouchers  CurrencyCode, ExchangeRate, NetSAR (the net in riyals: what is added to the accounts and summed in the lists)
 * CompanyProfile   PoSignsJson, VoucherSignsJson - the signature boxes of the two printed forms (title + name, any number)
Existing rows are riyal rows: the new columns are filled so they read the same as before.
"""
import db
from migrations._util import add_column

DESCRIPTION = "العملة وسعر الصرف وخيارات الضريبة لأمر الشراء وأمر الصرف، وتوقيعات قابلة للضبط"


def main():
    if db.table_exists("PurchaseOrders"):
        add_column("PurchaseOrders", "VatMode", "TEXT(10)")
        add_column("PurchaseOrders", "VatPct", "DOUBLE")
        add_column("PurchaseOrders", "CurrencyCode", "TEXT(10)")
        add_column("PurchaseOrders", "ExchangeRate", "DOUBLE")
        add_column("PurchaseOrders", "TotalCurrency", "DOUBLE")
        db.execute("UPDATE [PurchaseOrders] SET [VatMode] = 'add', [VatPct] = 15 WHERE [VatMode] IS NULL")
        db.execute("UPDATE [PurchaseOrders] SET [CurrencyCode] = 'SAR', [ExchangeRate] = 1 WHERE [CurrencyCode] IS NULL")
        db.execute("UPDATE [PurchaseOrders] SET [TotalCurrency] = [TotalAmount] WHERE [TotalCurrency] IS NULL")
    if db.table_exists("PaymentVouchers"):
        add_column("PaymentVouchers", "CurrencyCode", "TEXT(10)")
        add_column("PaymentVouchers", "ExchangeRate", "DOUBLE")
        add_column("PaymentVouchers", "NetSAR", "DOUBLE")
        db.execute("UPDATE [PaymentVouchers] SET [CurrencyCode] = 'SAR', [ExchangeRate] = 1 WHERE [CurrencyCode] IS NULL")
        db.execute("UPDATE [PaymentVouchers] SET [NetSAR] = [Net] WHERE [NetSAR] IS NULL")
    if db.table_exists("CompanyProfile"):
        add_column("CompanyProfile", "PoSignsJson", "MEMO")
        add_column("CompanyProfile", "VoucherSignsJson", "MEMO")
    return True

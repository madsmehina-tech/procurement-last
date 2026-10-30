# -*- coding: utf-8 -*-
"""
m031_voucher_posting - the simplified «طلب صرف» form and the choice «add this payment to the account statement?».

 * PaymentVouchers.ShowTotals   print the VAT / total box or not (the company's current form does not print it)
 * PaymentVouchers.PostChoice   «نعم» / «لا»: should the payment be added to the contractor's / supplier's account when the voucher is APPROVED
 * PaymentVouchers.PostedAt / PostedBy / PostedKind / PostedRef   what was posted, once (a voucher can never post twice)
 * CompanyProfile.SignContracts / SignProjects   the two new signatories (مدير العقود والمشتريات، مدير عام المشاريع); the accountant is no longer printed
The signatory names are filled in only where they are still empty, so a name you typed in «بيانات الشركة» is never overwritten.
"""
import db
from migrations._util import add_column

DESCRIPTION = "أمر الصرف المبسّط: إظهار الإجماليات، خيار إضافة الدفعة لكشف الحساب عند الاعتماد، وتوقيعا مدير العقود ومدير عام المشاريع"

DEFAULT_NAMES = {"SignContracts": "م محمود عدس", "SignProjects": "م محمد الجداوى", "SignAccounts": "محمد الفرماوي", "SignFinance": "محمد البشير", "SignGM": "صنهات العتيبي",
                 "VoucherDept": "إدارة العقود والمشتريات"}


def main():
    if db.table_exists("PaymentVouchers"):
        for col, typ in (("ShowTotals", "INTEGER"), ("PostChoice", "TEXT(3)"), ("PostedAt", "DATETIME"), ("PostedBy", "TEXT(50)"), ("PostedKind", "TEXT(20)"), ("PostedRef", "TEXT(255)")):
            add_column("PaymentVouchers", col, typ)
    if db.table_exists("CompanyProfile"):
        for col in ("SignContracts", "SignProjects"):
            add_column("CompanyProfile", col, "TEXT(150)")
        if db.query("SELECT [ProfileID] FROM [CompanyProfile] WHERE [ProfileID] = 1"):
            n = 0
            for col, name in DEFAULT_NAMES.items():
                if db.query("SELECT [ProfileID] FROM [CompanyProfile] WHERE [ProfileID] = 1 AND ([%s] IS NULL OR [%s] = '')" % (col, col)):
                    db.execute("UPDATE [CompanyProfile] SET [%s] = ? WHERE [ProfileID] = 1" % col, (name,))
                    n += 1
            print("- the signatory / department names filled where they were empty: %d" % n)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
m030_opening_vouchers - supplier account, payment vouchers («أمر صرف عام») and their permissions.

 * SupplierEntries   the movements typed by hand on a supplier's account: opening balance, payments, returns / credit notes, other dues
                     (the purchase orders themselves are NOT copied: the statement reads them from PurchaseOrders)
 * PaymentVouchers   one row per voucher (PR-0027 ...): beneficiary, project, payment method, the items and referenced documents (JSON text), the
                     money split (before VAT, VAT, total, discount, net) and the status مسودة / معتمد / ملغي
 * CompanyProfile    the four signatory names printed on the voucher + the default requesting department
 * the contractor opening balance needs NO new column: it is one LedgerEntries row (category «رصيد افتتاحي»), so every figure that reads the account
   (statement, contractor page, dashboards) includes it by itself.

Additive only; safe to run again. The permissions are granted to the existing roles that already do that kind of work.
"""
import db
from migrations._util import add_column

DESCRIPTION = "كشف حساب المورد، أوامر الصرف العامة (PR)، وتوقيعات النموذج، وصلاحيات الطباعة"

TABLES = {
    "SupplierEntries": "CREATE TABLE [SupplierEntries] ([EntryID] AUTOINCREMENT PRIMARY KEY, [SupplierID] INTEGER, [Kind] TEXT(10), [Category] TEXT(40), [Description] TEXT(255), "
                       "[Amount] CURRENCY, [EntryDate] DATETIME, [ProjectName] TEXT(255), [PONumber] TEXT(50), [BankRef] TEXT(80), [CreatedBy] TEXT(50), [CreatedAt] DATETIME)",
    "PaymentVouchers": "CREATE TABLE [PaymentVouchers] ([VoucherID] AUTOINCREMENT PRIMARY KEY, [VoucherNo] TEXT(20), [VoucherDate] DATETIME, [Status] TEXT(12), "
                       "[ProjectID] INTEGER, [ProjectName] TEXT(255), [CostCenter] TEXT(255), [PartyKind] TEXT(10), [SupplierID] INTEGER, [SubcontractorID] INTEGER, [PartyName] TEXT(255), "
                       "[RequestType] TEXT(120), [Department] TEXT(120), [PayMethod] TEXT(40), [PayStatement] TEXT(255), [Description] TEXT(255), "
                       "[AmountMode] TEXT(6), [VatPct] DOUBLE, [Subtotal] CURRENCY, [VatAmount] CURRENCY, [Total] CURRENCY, [Discount] CURRENCY, [Net] CURRENCY, "
                       "[ItemsJson] MEMO, [RefsJson] MEMO, [SignJson] MEMO, [Notes] MEMO, "
                       "[CreatedBy] TEXT(50), [CreatedAt] DATETIME, [ModifiedAt] DATETIME, [ApprovedBy] TEXT(50), [ApprovedAt] DATETIME, [CancelledBy] TEXT(50), [CancelledAt] DATETIME)",
}

# role key -> permissions to grant (only if the role exists)
GRANTS = {
    "finance": ["vouchers.view", "vouchers.create", "vouchers.approve", "accounts.opening", "suppliers.account", "suppliers.view"],
    "procurement": ["vouchers.view", "vouchers.create"],
    "contracts": ["vouchers.view", "vouchers.create"],
}


def main():
    for name, sql in TABLES.items():
        if db.table_exists(name):
            print("- %s: already exists, unchanged" % name)
        else:
            db.execute(sql)
            print("- %s: created" % name)
    if db.table_exists("CompanyProfile"):
        for col in ("SignAccountant", "SignAccounts", "SignFinance", "SignGM", "VoucherDept"):
            add_column("CompanyProfile", col, "TEXT(150)")
    try:
        db.execute("CREATE UNIQUE INDEX [ux_voucher_no] ON [PaymentVouchers] ([VoucherNo])")
        print("- PaymentVouchers.VoucherNo: unique index created")
    except Exception as e:                               # noqa - already there is fine
        msg = " ".join(str(e).split())[:120]
        print("- PaymentVouchers.VoucherNo: %s" % ("already exists" if "already exists" in msg.lower() or "موجود" in msg else "skipped (%s)" % msg))
    if not db.table_exists("Roles"):
        return
    n = 0
    for r in db.query("SELECT [RoleID], [RoleKey] FROM [Roles]"):
        for perm in GRANTS.get(r["RoleKey"], []):
            if not db.query("SELECT [RoleID] FROM [RolePermissions] WHERE [RoleID] = ? AND [PermCode] = ?", (r["RoleID"], perm)):
                db.execute("INSERT INTO [RolePermissions] ([RoleID], [PermCode]) VALUES (?, ?)", (r["RoleID"], perm))
                n += 1
    print("- voucher / opening-balance permissions granted to the existing roles: %d" % n)


if __name__ == "__main__":
    main()

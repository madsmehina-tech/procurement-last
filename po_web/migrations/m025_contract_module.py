# -*- coding: utf-8 -*-
r"""
m025 - the CONTRACT MODULE: contracts generated from Word templates, for contractors AND suppliers.

 * legal data of the parties (address, e-mail, representative and his title, unified number, bank, account holder),
   read by the contract instead of being retyped (Subcontractors, Suppliers); the first party's data (CompanyProfile);
 * Contracts: type, party kind (مقاول / مورد) + SupplierID, origin (مؤرشف = signed outside the system / مولَّد = made by the system),
   scope, price model and the value split (without VAT / VAT), duration, delay penalty, template version, revision, source PO;
 * ContractLines (price schedule / specification rows), ContractTranches (payments), ContractData (template-specific fields),
   ContractHistory (what happened to the contract);
 * ContractTemplates + ContractTemplateVersions (the Word templates and their versions), NumberSeries (SC-2026-007).

Additive only: existing contracts keep working (origin «مؤرشف», party kind «مقاول»). Safe to run again.
"""
import db
from migrations._util import add_column

DESCRIPTION = "وحدة العقود: عقود من قوالب Word للمقاولين والموردين"

COMPANY_SEED = {"LegalName": "شركة الهضب للتجارة والمقاولات", "CR": "1010010305", "City": "الرياض", "RepName": "صنهات حمد العتيبي", "RepTitle": "المدير العام"}


def main():
    for col, typ in (("Address", "TEXT(255)"), ("Email", "TEXT(120)"), ("RepName", "TEXT(120)"), ("RepTitle", "TEXT(80)"), ("UnifiedNo", "TEXT(30)"), ("BankName", "TEXT(80)"), ("AccountHolder", "TEXT(150)")):
        add_column("Subcontractors", col, typ)
    for col, typ in (("Address", "TEXT(255)"), ("RepTitle", "TEXT(80)"), ("UnifiedNo", "TEXT(30)"), ("BankName", "TEXT(80)"), ("AccountHolder", "TEXT(150)")):
        add_column("Suppliers", col, typ)
    for col, typ in (("PartyKind", "TEXT(10)"), ("SupplierID", "INTEGER"), ("ContractType", "TEXT(60)"), ("Origin", "TEXT(12)"), ("Scope", "MEMO"), ("PriceModel", "TEXT(20)"),
                     ("ValueExVat", "DOUBLE"), ("VatAmount", "DOUBLE"), ("DurationDays", "INTEGER"), ("PenaltyPerDay", "DOUBLE"), ("PenaltyMaxPct", "DOUBLE"), ("WarrantyText", "TEXT(255)"),
                     ("TemplateVersionID", "INTEGER"), ("Revision", "INTEGER"), ("SourcePO", "TEXT(40)"), ("SourceCompareID", "INTEGER"), ("ContractDate", "DATETIME"),
                     ("QuoteNo", "TEXT(60)"), ("QuoteDate", "DATETIME"), ("GeneratedAt", "DATETIME"), ("GeneratedBy", "TEXT(50)")):
        add_column("Contracts", col, typ)
    tables = {
        "CompanyProfile": "CREATE TABLE [CompanyProfile] ([ProfileID] INTEGER, [LegalName] TEXT(150), [CR] TEXT(30), [VatNo] TEXT(30), [UnifiedNo] TEXT(30), [Address] TEXT(255), [City] TEXT(60), "
                          "[Phone] TEXT(40), [Email] TEXT(120), [RepName] TEXT(120), [RepTitle] TEXT(80), [BankName] TEXT(80), [IBAN] TEXT(40), [AccountHolder] TEXT(150), [UpdatedAt] DATETIME)",
        "ContractLines": "CREATE TABLE [ContractLines] ([LineID] AUTOINCREMENT PRIMARY KEY, [ContractID] INTEGER, [Kind] TEXT(12), [LineNo] INTEGER, [Description] TEXT(255), [Unit] TEXT(30), "
                         "[Qty] DOUBLE, [UnitPrice] DOUBLE, [Note] MEMO)",
        "ContractTranches": "CREATE TABLE [ContractTranches] ([TrancheID] AUTOINCREMENT PRIMARY KEY, [ContractID] INTEGER, [TrancheNo] INTEGER, [Label] TEXT(80), [Pct] DOUBLE, [Amount] DOUBLE, [DueText] TEXT(255))",
        "ContractData": "CREATE TABLE [ContractData] ([DataID] AUTOINCREMENT PRIMARY KEY, [ContractID] INTEGER, [FieldKey] TEXT(120), [FieldValue] MEMO)",
        "ContractHistory": "CREATE TABLE [ContractHistory] ([HistID] AUTOINCREMENT PRIMARY KEY, [ContractID] INTEGER, [At] DATETIME, [Action] TEXT(40), [Detail] TEXT(255), [ByUser] TEXT(50))",
        "ContractTemplates": "CREATE TABLE [ContractTemplates] ([TemplateID] AUTOINCREMENT PRIMARY KEY, [Code] TEXT(30), [Name] TEXT(120), [ContractType] TEXT(60), [PartyKind] TEXT(10), [NumberPrefix] TEXT(10), "
                             "[Description] TEXT(255), [Active] INTEGER, [CreatedBy] TEXT(50), [CreatedAt] DATETIME)",
        "ContractTemplateVersions": "CREATE TABLE [ContractTemplateVersions] ([VersionID] AUTOINCREMENT PRIMARY KEY, [TemplateID] INTEGER, [VersionNo] INTEGER, [FileName] TEXT(255), [StoredPath] TEXT(255), "
                                    "[VariablesJson] MEMO, [ChangeNote] TEXT(255), [IsCurrent] INTEGER, [CreatedBy] TEXT(50), [CreatedAt] DATETIME)",
        "NumberSeries": "CREATE TABLE [NumberSeries] ([SeriesKey] TEXT(30), [SeriesYear] INTEGER, [LastNo] INTEGER)",
    }
    for name, ddl in tables.items():
        if db.table_exists(name):
            print("- %s: already exists, unchanged" % name)
        else:
            db.execute(ddl)
            print("- %s: created" % name)
    if not db.query("SELECT [ProfileID] FROM [CompanyProfile] WHERE [ProfileID] = 1"):
        from datetime import datetime
        s = COMPANY_SEED
        db.execute("INSERT INTO [CompanyProfile] ([ProfileID], [LegalName], [CR], [City], [RepName], [RepTitle], [UpdatedAt]) VALUES (1, ?, ?, ?, ?, ?, ?)",
                   (s["LegalName"], s["CR"], s["City"], s["RepName"], s["RepTitle"], datetime.now()))
        print("- CompanyProfile: the first party's data added (edit it in Settings -> بيانات الشركة)")
    n = db.scalar("SELECT COUNT(*) FROM [Contracts] WHERE [Origin] IS NULL", (), 0)
    if n:
        db.execute("UPDATE [Contracts] SET [Origin] = ? WHERE [Origin] IS NULL", ("مؤرشف",))
        db.execute("UPDATE [Contracts] SET [PartyKind] = ? WHERE [PartyKind] IS NULL", ("مقاول",))
    print("- existing contracts marked «مؤرشف»: %d" % n)


if __name__ == "__main__":
    main()

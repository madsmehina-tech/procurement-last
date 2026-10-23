# -*- coding: utf-8 -*-
r"""
m024 - quote comparison (مقارنة عروض الأسعار), part of the purchasing cycle BEFORE the purchase order / the contract.

    Comparisons      one comparison: title, project, site, kind (مواد / مقاولين / خدمات), status, the award decision
    ComparisonItems  the UNIFIED items of a comparison (one row per item the offers are compared on) with the required quantity
    Quotes           one offer per supplier / contractor: number, date, VAT included or not, terms, the original file
    QuoteLines       the lines of an offer AS WRITTEN + the unified item each one belongs to + the confidence of the match
    ItemAliases      what the system LEARNED from the confirmed matches (a name written one way = this unified item)

Nothing existing is changed. Safe to run again.
"""
import db

DESCRIPTION = "مقارنة عروض الأسعار: المقارنات والبنود الموحدة والعروض وسطورها وأسماء البنود المتعلَّمة"


def main():
    tables = {
        "Comparisons": "CREATE TABLE [Comparisons] ([CompareID] AUTOINCREMENT PRIMARY KEY, [Title] TEXT(150), [Kind] TEXT(20), [ProjectID] INTEGER, [SiteID] INTEGER, "
                       "[ProjectName] TEXT(150), [SiteName] TEXT(150), [Description] TEXT(255), [Status] TEXT(20), [WinnerQuoteID] INTEGER, [AwardReason] TEXT(255), "
                       "[WinnerTotal] DOUBLE, [SavingsVsAvg] DOUBLE, [SavingsVsMax] DOUBLE, [AwardedAt] DATETIME, [AwardedBy] TEXT(50), [NextKind] TEXT(20), [NextRef] TEXT(60), "
                       "[Confirmed] INTEGER, [ArchiveFolder] TEXT(255), [CreatedBy] TEXT(50), [CreatedAt] DATETIME)",
        "ComparisonItems": "CREATE TABLE [ComparisonItems] ([ItemID] AUTOINCREMENT PRIMARY KEY, [CompareID] INTEGER, [SortNo] INTEGER, [Name] TEXT(255), [Unit] TEXT(30), "
                           "[RequiredQty] DOUBLE, [QtyFixed] INTEGER, [CatalogName] TEXT(255))",
        "Quotes": "CREATE TABLE [Quotes] ([QuoteID] AUTOINCREMENT PRIMARY KEY, [CompareID] INTEGER, [PartyKind] TEXT(12), [SupplierID] INTEGER, [SubcontractorID] INTEGER, "
                  "[PartyName] TEXT(150), [QuoteNo] TEXT(60), [QuoteDate] DATETIME, [VatIncluded] TEXT(8), [PaymentTerms] TEXT(150), [DeliveryDays] INTEGER, [ValidUntil] DATETIME, "
                  "[DeclaredTotal] DOUBLE, [Engine] TEXT(20), [OriginalFile] TEXT(255), [Notes] TEXT(255), [CreatedAt] DATETIME)",
        "QuoteLines": "CREATE TABLE [QuoteLines] ([LineID] AUTOINCREMENT PRIMARY KEY, [QuoteID] INTEGER, [LineNo] INTEGER, [RawName] TEXT(255), [Unit] TEXT(30), [Qty] DOUBLE, "
                      "[UnitPrice] DOUBLE, [ItemID] INTEGER, [Confidence] DOUBLE, [Locked] INTEGER, [Note] TEXT(255))",
        "ItemAliases": "CREATE TABLE [ItemAliases] ([AliasID] AUTOINCREMENT PRIMARY KEY, [AliasKey] TEXT(255), [Name] TEXT(255), [Unit] TEXT(30), [UseCount] INTEGER, [LastUsedAt] DATETIME)",
    }
    for name, ddl in tables.items():
        if db.table_exists(name):
            print("- %s: already exists, unchanged" % name)
        else:
            db.execute(ddl)
            print("- %s: created" % name)


if __name__ == "__main__":
    main()

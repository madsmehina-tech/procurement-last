# -*- coding: utf-8 -*-
"""
m027_settlement_link - LedgerEntries.SettledCertID.

When a transaction is closed in the archive while a balance is still unpaid, the system writes ONE visible line in the contractor's account statement
(«تسوية عند إقفال المعاملة», a credit for the unpaid balance) so the contractor's figures show that balance as settled. This column remembers WHICH certificate
that line settled, so that reopening the transaction removes exactly that line and nothing else.
"""
from migrations._util import add_column


def main():
    add_column("LedgerEntries", "SettledCertID", "INTEGER")

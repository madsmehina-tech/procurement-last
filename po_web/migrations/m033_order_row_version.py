# -*- coding: utf-8 -*-
"""
m033_order_row_version - a whole-number version on every purchase order (PurchaseOrders.RowVersion), raised by every edit of the order.
The edit page carries the number it was opened with; the save compares it INSIDE the transaction, in the UPDATE itself, so two people saving
in the same second can no longer overwrite each other. Existing orders start at 0.
"""
import db
from migrations._util import add_column

DESCRIPTION = "رقم إصدار لكل أمر شراء لكشف تعارض التعديل بين موظفين"


def main():
    if not db.table_exists("PurchaseOrders"):
        print("- PurchaseOrders: not there yet")
        return True
    add_column("PurchaseOrders", "RowVersion", "INTEGER")
    db.execute("UPDATE [PurchaseOrders] SET [RowVersion] = 0 WHERE [RowVersion] IS NULL")
    return True

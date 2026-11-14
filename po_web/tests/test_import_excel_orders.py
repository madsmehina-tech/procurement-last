# -*- coding: utf-8 -*-
"""The tool that completes the system from the old Excel tracker: adds the missing orders with their items, gives an order without items its items, and changes nothing else."""
import os
import sys
from datetime import datetime

import pytest

import conftest
import fake_db

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import import_excel_orders as tool


@pytest.fixture(autouse=True)
def _schema():
    conftest.build_schema()


@pytest.fixture()
def xlsx(tmp_path):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = tool.SHEET_ORDERS
    ws.append(["متابعة أوامر الشراء"])
    ws.append(["م", "أمر الشراء", "التاريخ", "المورد", "عرض السعر", "المشروع", "إجمالي قيمة أمر الشراء", "سند الاستلام", "الفاتورة", "السداد"])
    ws.append([1, "PO-2026-443", datetime(2026, 10, 18), "مورد قديم", "", "مشروع أ", 115, "معلق", "معلق", "معلق"])
    ws.append([2, "PO-2026-444", datetime(2026, 10, 19), "صروح", "Q1", "مشروع أ", 96140, "معلق", "معلق", "تم السداد"])
    ws.append([3, "PO-2026-445", datetime(2026, 10, 20), "الخضراء العربية", "", "مشروع ب", 248503.5, "معلق", "معلق", "معلق"])
    ws.append([4, "PO-2026-446", datetime(2026, 10, 20), "عنوان الفولاذ", "2026-618", "مشروع أ", 13072.4, "معلق", "تم الإصدار", "تم السداد"])
    it = wb.create_sheet(tool.SHEET_ITEMS)
    it.append(["الأصناف"])
    it.append(["م", "اسم الصنف", "سعر الصنف", "المورد", "الكمية", "رقم أمر الشراء", "التاريخ", "الوحدة", "الإجمالي"])
    it.append([1, "صنف قديم", 100, "مورد قديم", 1, "PO-2026-443", None, "عدد", 100])
    it.append([2, "موقف دراجات\nسطر ثان", 3800, "صروح", 22, "PO-2026-444", None, "", 83600])
    it.append([3, "أرضيات مطاطية", 245, "الخضراء العربية", 882, "PO-2026-445", None, "م2", 216090])
    it.append([4, "SHEET 16MM", 4672, "عنوان الفولاذ", 2, "PO-2026-446", None, "Pcs", 9344])
    it.append([5, 'PIPE 4"', 253, "عنوان الفولاذ", 8, "PO-2026-446", None, "PCS", 2024])
    hd = wb.create_sheet(tool.SHEET_HDR)
    hd.append(["PO", "Receiver", "Phone"])
    hd.append(["po-2026-445", "م / بيتر", "0568177493"])
    path = str(tmp_path / "t.xlsx")
    wb.save(path)
    now = datetime.now()
    fake_db.execute("INSERT INTO [PurchaseOrders] ([PONumber], [OrderDate], [SupplierName], [Project], [TotalAmount], [PaymentStatus], [CreatedAt]) VALUES ('PO-2026-443', ?, 'مورد قديم', 'مشروع أ', 999, 'معلق', ?)", (now, now))
    fake_db.execute("INSERT INTO [OrderItems] ([PONumber], [LineNo], [ItemName], [Unit], [Qty], [UnitPrice], [QtyReceived]) VALUES ('PO-2026-443', 1, 'في النظام', 'عدد', 1, 999, 0)")
    fake_db.execute("INSERT INTO [PurchaseOrders] ([PONumber], [OrderDate], [SupplierName], [Project], [TotalAmount], [PaymentStatus], [CreatedAt]) VALUES ('PO-2026-444', ?, 'صروح', 'مشروع أ', 127650, 'معلق', ?)", (now, now))
    return path


def _orders():
    return {r["PONumber"]: r for r in fake_db.query("SELECT * FROM [PurchaseOrders]")}


def _items(po):
    return sorted(fake_db.query("SELECT * FROM [OrderItems] WHERE [PONumber] = ?", (po,)), key=lambda r: r["LineNo"])


def test_the_preview_says_what_would_happen_and_writes_nothing(xlsx, capsys):
    assert tool.main([xlsx]) == 0
    out = capsys.readouterr().out
    assert "تُضاف): 2" in out and "PO-2026-445" in out and "PO-2026-446" in out and "أصنافها فقط، إجماليها لا يتغير): 1" in out and "لا تُمس: 1" in out and "معاينة فقط" in out
    assert "PO-2026-444: إجماليه في النظام 127650.00 يبقى كما هو" in out
    assert len(_orders()) == 2 and len(fake_db.query("SELECT * FROM [OrderItems]")) == 1


def test_apply_adds_the_missing_orders_with_their_items_and_only_completes_the_one_without_items(xlsx):
    assert tool.main([xlsx, "--apply", "--yes"]) == 0
    o = _orders()
    assert set(o) == {"PO-2026-443", "PO-2026-444", "PO-2026-445", "PO-2026-446"}
    a = o["PO-2026-445"]
    assert a["TotalAmount"] == 248503.5 and a["TotalCurrency"] == 248503.5 and a["VatMode"] == "add" and a["CurrencyCode"] == "SAR" and a["ExchangeRate"] == 1
    assert a["Receiver"] == "م / بيتر" and a["ReceiverPhone"] == "0568177493" and a["SupplierID"] and a["ProjectID"] and a["HijriDate"]
    assert [r["Name"] for r in fake_db.query("SELECT [Name] FROM [Suppliers]")].count("الخضراء العربية") == 1
    b = o["PO-2026-446"]
    assert (b["InvoiceStatus"], b["PaymentStatus"], b["ReceiptStatus"], b["QuoteNo"]) == ("تم الإصدار", "تم السداد", "معلق", "2026-618")
    assert [(i["LineNo"], i["ItemName"], i["Qty"], i["UnitPrice"], i["Unit"]) for i in _items("PO-2026-446")] == [(1, "SHEET 16MM", 2, 4672, "Pcs"), (2, 'PIPE 4"', 8, 253, "PCS")]
    # the order that was in the system without items: the items came, the total and the status stayed
    assert o["PO-2026-444"]["TotalAmount"] == 127650 and o["PO-2026-444"]["PaymentStatus"] == "معلق"
    assert [(i["ItemName"], i["Qty"], i["UnitPrice"]) for i in _items("PO-2026-444")] == [("موقف دراجات\nسطر ثان", 22, 3800)]
    # the order that already had items is untouched
    assert o["PO-2026-443"]["TotalAmount"] == 999 and [i["ItemName"] for i in _items("PO-2026-443")] == ["في النظام"]


def test_running_it_twice_adds_nothing_the_second_time(xlsx, capsys):
    tool.main([xlsx, "--apply", "--yes"])
    n_orders, n_items = len(_orders()), len(fake_db.query("SELECT * FROM [OrderItems]"))
    capsys.readouterr()
    assert tool.main([xlsx, "--apply", "--yes"]) == 0
    assert "لا شيء يُضاف" in capsys.readouterr().out
    assert (len(_orders()), len(fake_db.query("SELECT * FROM [OrderItems]"))) == (n_orders, n_items)


def test_only_limits_the_work_and_reports_a_number_that_is_not_in_excel(xlsx, capsys):
    tool.main([xlsx, "--only", "po-2026-445, PO-2026-999", "--apply", "--yes"])
    assert "«po2026999» غير موجود" in capsys.readouterr().out
    assert set(_orders()) == {"PO-2026-443", "PO-2026-444", "PO-2026-445"} and _items("PO-2026-444") == []


def test_a_file_that_is_not_the_tracker_is_refused(tmp_path):
    from openpyxl import Workbook
    p = str(tmp_path / "x.xlsx")
    Workbook().save(p)
    with pytest.raises(SystemExit):
        tool.main([p])

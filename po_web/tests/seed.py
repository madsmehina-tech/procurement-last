# -*- coding: utf-8 -*-
"""A realistic amount of data for speed tests: 15 contractors, 400 certificates, 600 payments, 300 purchase orders."""
import random
from datetime import datetime, timedelta

import fake_db
import helpers as h


def seed(n_subs=15, certs_per_sub=27, n_orders=300, seed_value=7):
    rnd = random.Random(seed_value)
    now = datetime.now()
    subs = [h.add_sub("مقاول %d" % i) for i in range(n_subs)]
    projects = ["مشروع %d" % i for i in range(1, 7)]
    cert_ids = []
    for s_ in subs:
        for k in range(certs_per_sub):
            proj = rnd.choice(projects)
            pid = h.project_id(proj)
            total = rnd.randint(20, 400) * 1000.0
            cid = h.add_cert(s_, total, no=k + 1, site="موقع %d" % rnd.randint(1, 4), ProjectID=pid)
            cert_ids.append((cid, s_, total))
            if rnd.random() < 0.55:
                part = total if rnd.random() < 0.6 else round(total * 0.4, 2)
                for j in range(rnd.randint(1, 2)):
                    fake_db.execute("INSERT INTO [Payments] ([CertificateID], [SubcontractorID], [PaidAt], [Amount], [CreatedAt]) VALUES (?, ?, ?, ?, ?)",
                                    (cid, s_, now - timedelta(days=rnd.randint(1, 200)), round(part / 2, 2), now))
                if part == total:
                    fake_db.execute("UPDATE [Certificates] SET [Status] = 'مصروف' WHERE [CertificateID] = ?", (cid,))
    sup = [fake_db.execute_insert("INSERT INTO [Suppliers] ([Name], [CreatedAt]) VALUES (?, ?)", ("مورد %d" % i, now)) for i in range(25)]
    for i in range(n_orders):
        po = "PO-2026-%03d" % (i + 1)
        fake_db.execute("INSERT INTO [PurchaseOrders] ([PONumber], [OrderDate], [SupplierName], [Project], [TotalAmount], [ReceiptStatus], [InvoiceStatus], [PaymentStatus], [CreatedAt], [ModifiedAt]) "
                        "VALUES (?, ?, ?, ?, ?, 'لم يبدأ', 'معلق', 'معلق', ?, ?)", (po, now - timedelta(days=rnd.randint(0, 300)), "مورد %d" % rnd.randint(0, 24), rnd.choice(projects), rnd.randint(1, 90) * 1000.0, now, now))
        for ln in range(1, 6):
            fake_db.execute("INSERT INTO [OrderItems] ([PONumber], [LineNo], [ItemName], [Unit], [Qty], [UnitPrice], [QtyReceived]) VALUES (?, ?, ?, 'عدد', ?, ?, 0)",
                            (po, ln, "صنف %d" % rnd.randint(1, 120), rnd.randint(1, 50), rnd.randint(5, 900)))
    return subs

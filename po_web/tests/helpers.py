# -*- coding: utf-8 -*-
"""Small builders so each test reads like the story it checks."""
from datetime import datetime

import fake_db


def add_sub(name="مقاول تجريبي"):
    return fake_db.execute_insert("INSERT INTO [Subcontractors] ([Name], [CreatedAt]) VALUES (?, ?)", (name, datetime.now()))


def project_id(name="مشروع 1"):
    rows = fake_db.query("SELECT [ProjectID] FROM [Projects] WHERE [ProjectName] = ?", (name,))
    if rows:
        return rows[0]["ProjectID"]
    return fake_db.execute_insert("INSERT INTO [Projects] ([ProjectName], [NameKey], [IsActive], [CreatedAt]) VALUES (?, ?, 1, ?)", (name, name, datetime.now()))


def add_cert(sub_id, total=100000.0, no=1, status="مُدخَل", site="موقع 1", **extra):
    """A certificate in the approved v2 model: value incl. VAT = total, no deductions."""
    cols = {"SubcontractorID": sub_id, "ProjectID": project_id(), "SiteName": site, "CertificateNo": no, "CertificateDate": datetime.now(), "GrossAmount": round(total / 1.15, 2),
            "TotalInclVat": total, "SuppliedMaterials": 0.0, "Penalties": 0.0, "RetentionPercent": 0, "AmountModel": "v2", "Status": status,
            "CreatedAt": datetime.now()}
    cols.update(extra)
    names = ", ".join("[%s]" % c for c in cols)
    return fake_db.execute_insert("INSERT INTO [Certificates] (%s) VALUES (%s)" % (names, ", ".join("?" for _ in cols)), tuple(cols.values()))


def payments(cert_id):
    return fake_db.query("SELECT * FROM [Payments] WHERE [CertificateID] = ?", (cert_id,))


def paid(cert_id):
    return round(sum(p["Amount"] for p in payments(cert_id)), 2)


def cert_row(cert_id):
    return fake_db.query("SELECT * FROM [Certificates] WHERE [CertificateID] = ?", (cert_id,))[0]


def cycle_to_execution(decisions):
    """Creates a cycle for every open certificate and moves it to stage 3 (execution).
    decisions: {cert_id: ("صرف كامل" | "صرف جزئي" | "تأجيل", partial_amount_or_None)}. Returns the cycle id."""
    import cycles_service as cy
    cid = cy.create("اختبار", datetime.now(), "tester")
    cy.to_decision_stage(cid, "tester")
    lines = fake_db.query("SELECT * FROM [CycleDecisions] WHERE [CycleID] = ?", (cid,))
    entries = {}
    for d in lines:
        dec, part = decisions[d["CertificateID"]]
        entries[d["DecisionID"]] = (dec, part, "")
    cy.save_lines(cid, entries, "tester")
    cy.to_execution_stage(cid, "tester")
    return cid

# -*- coding: utf-8 -*-
r"""
m023 - the archive of payments and financial movements.

1. Every payment gets a STORED number per contractor («دفعة رقم N») when it is executed - it never changes afterwards, so the folder
   name, the payment order and the account statement always agree: Payments.PayNo and LedgerEntries.PayNo.
2. The folder of a payment / movement: Payments.ArchiveFolder and LedgerEntries.ArchiveFolder, decided once:
     إدارة مقاولي الباطن / <المقاول> / الدفعات والحركات المالية / <السنة> / دفعة رقم N - <التاريخ>
3. Existing payments are numbered chronologically per contractor (a cycle transfer = one payment).
4. The transfer receipt of a certificate is the document type «سند الصرف» (the fifth paper of the transaction); the type
   «معاملة المستخلص» uploaded in the last versions is renamed to it.
Nothing is deleted. Safe to run again.
"""
import db
from migrations._util import add_column

DESCRIPTION = "أرشفة الدفعات: رقم الدفعة وفولدرها وسند الصرف"


def main():
    add_column("Payments", "PayNo", "INTEGER")
    add_column("Payments", "ArchiveFolder", "TEXT(255)")
    add_column("LedgerEntries", "PayNo", "INTEGER")
    add_column("LedgerEntries", "ArchiveFolder", "TEXT(255)")
    pays = db.query("SELECT * FROM [Payments]")
    entries = [e for e in db.query("SELECT * FROM [LedgerEntries]") if e["Kind"] == "دائن" and e["Category"] == "دفعة مباشرة" and e["Status"] == "منفّذة"]
    certs = {g["CertificateID"]: g.get("SubcontractorID") for g in db.query("SELECT [CertificateID], [SubcontractorID], [ContractID] FROM [Certificates]")}
    contracts = {c["ContractID"]: c.get("SubcontractorID") for c in db.query("SELECT [ContractID], [SubcontractorID] FROM [Contracts]")}
    for g in db.query("SELECT [CertificateID], [ContractID], [SubcontractorID] FROM [Certificates]"):
        certs[g["CertificateID"]] = g.get("SubcontractorID") or contracts.get(g.get("ContractID"))
    events = {}                                         # (sub, kind...) -> {"date", "pays": [...], "entries": [...]}
    for p in pays:
        sub = p.get("SubcontractorID") or certs.get(p["CertificateID"])
        when = p.get("PaidAt")
        key = (sub, "c", p["CycleID"], when.date() if when else None) if p.get("CycleID") else (sub, "p", p["PaymentID"])
        ev = events.setdefault(key, {"sub": sub, "date": when, "pays": [], "entries": []})
        ev["pays"].append(p)
    for e in entries:
        when = e.get("EntryDate")
        key = (e["SubcontractorID"], "c", e["CycleID"], when.date() if when else None) if e.get("CycleID") else (e["SubcontractorID"], "e", e["EntryID"])
        ev = events.setdefault(key, {"sub": e["SubcontractorID"], "date": when, "pays": [], "entries": []})
        ev["entries"].append(e)
    from datetime import datetime
    per_sub = {}
    for key, ev in events.items():
        per_sub.setdefault(ev["sub"], []).append(ev)
    numbered = 0
    for sub, evs in per_sub.items():
        evs.sort(key=lambda ev: (ev["date"] or datetime.min, (ev["pays"] or ev["entries"])[0].get("PaymentID") or (ev["pays"] or ev["entries"])[0].get("EntryID") or 0))
        base = max([p.get("PayNo") or 0 for ev in evs for p in ev["pays"]] + [e.get("PayNo") or 0 for ev in evs for e in ev["entries"]] + [0])
        for ev in evs:
            have = [x.get("PayNo") for x in ev["pays"] + ev["entries"] if x.get("PayNo")]
            if have:
                continue                                 # already numbered (the migration ran before)
            base += 1
            for p in ev["pays"]:
                db.execute("UPDATE [Payments] SET [PayNo] = ? WHERE [PaymentID] = ?", (base, p["PaymentID"]))
            for e in ev["entries"]:
                db.execute("UPDATE [LedgerEntries] SET [PayNo] = ? WHERE [EntryID] = ?", (base, e["EntryID"]))
            numbered += 1
    print("- payments numbered per contractor: %d" % numbered)
    n = db.scalar("SELECT COUNT(*) FROM [Documents] WHERE [DocType] = ?", ("معاملة المستخلص",), 0)
    if n:
        db.execute("UPDATE [Documents] SET [DocType] = ? WHERE [DocType] = ?", ("سند الصرف", "معاملة المستخلص"))
    print("- «معاملة المستخلص» documents renamed to «سند الصرف»: %d" % n)


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
r"""
m018 - payment cycles, the archive of payment transactions, contracts linked to the project / site records.

1. PaymentCycles / CycleDecisions: the columns the three cycle screens need (title, date, who, folder, sent to finance;
   requested / approved amounts, decider). Short text columns are used (not MEMO) for what people type.
2. Certificates.ClosedAt / ClosedBy: a transaction closed in the archive.
3. Contracts.ProjectID and ContractSites.SiteRefID: contracts follow the same project / site records as the orders,
   so a report by project or site covers purchases AND subcontractor work. Existing contracts are linked.
Safe to run again.
"""
import db
import masterdata
from migrations._util import add_column

DESCRIPTION = "دورات الصرف وأرشيف معاملات الصرف وربط العقود بالمشروع والموقع"


def schema():
    for col, ty in (("Title", "TEXT(150)"), ("CycleDate", "DATETIME"), ("CreatedBy", "TEXT(50)"), ("Notes", "TEXT(255)"),
                    ("ArchiveFolder", "TEXT(255)"), ("SentToFinanceAt", "DATETIME"), ("ClosedAt", "DATETIME")):
        add_column("PaymentCycles", col, ty)
    for col, ty in (("RequestedAmount", "DOUBLE"), ("ApprovedAmount", "DOUBLE"), ("DecidedBy", "TEXT(50)"), ("DecidedAt", "DATETIME"),
                    ("NoteText", "TEXT(255)")):
        add_column("CycleDecisions", col, ty)
    add_column("Certificates", "ClosedAt", "DATETIME")
    add_column("Certificates", "ClosedBy", "TEXT(50)")
    add_column("Contracts", "ProjectID", "INTEGER")
    add_column("ContractSites", "SiteRefID", "INTEGER")


def link_contracts():
    n_c = n_s = 0
    for c in db.query("SELECT [ContractID], [Project], [SiteName], [ProjectID] FROM [Contracts]"):
        pid, pname, _new = masterdata.resolve_project(c.get("Project"))
        if pid and (c.get("ProjectID") != pid or pname != c.get("Project")):
            db.execute("UPDATE [Contracts] SET [ProjectID] = ?, [Project] = ? WHERE [ContractID] = ?", (pid, pname, c["ContractID"]))
            n_c += 1
        for s in db.query("SELECT [SiteID], [SiteName], [SiteRefID] FROM [ContractSites] WHERE [ContractID] = ?", (c["ContractID"],)):
            sid, sname, _n = masterdata.resolve_site(pid, s.get("SiteName"))
            if sid and (s.get("SiteRefID") != sid or sname != s.get("SiteName")):
                db.execute("UPDATE [ContractSites] SET [SiteRefID] = ?, [SiteName] = ? WHERE [SiteID] = ?", (sid, sname, s["SiteID"]))
                n_s += 1
    print("- contracts linked to their project record: %d | contract sites linked to site records: %d" % (n_c, n_s))


def grant_permissions():
    """The finance role closes payment transactions (a new permission added in this version)."""
    if not db.table_exists("Roles"):
        return
    n = 0
    for r in db.query("SELECT [RoleID], [RoleKey] FROM [Roles]"):
        if r["RoleKey"] == "finance" and not db.query("SELECT [RoleID] FROM [RolePermissions] WHERE [RoleID] = ? AND [PermCode] = ?", (r["RoleID"], "archive.close")):
            db.execute("INSERT INTO [RolePermissions] ([RoleID], [PermCode]) VALUES (?, ?)", (r["RoleID"], "archive.close"))
            n += 1
    print("- permission «إقفال معاملة الصرف» granted to the finance role: %d" % n)


def main():
    schema()
    link_contracts()
    grant_permissions()


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
r"""
m021 - the certificate no longer depends on a contract.

A certificate now carries its OWN contractor, project and site (Certificates.SubcontractorID / ProjectID / SiteID), taken
from the same unified project and site lists as the orders and the contracts. The contract becomes an optional link
(Certificates.ContractID may stay empty), so a certificate can be entered even when no contract is registered.
Existing certificates are filled from their contract, so nothing changes for them. The document type «ملف المستخلص النهائي» becomes
«ملف المستخلص من الموقع». Nothing is deleted. Safe to run again.
"""
import db
import masterdata
from migrations._util import add_column

DESCRIPTION = "المستخلص مستقل عن العقد: مقاول ومشروع وموقع خاصة به"


def schema():
    for col in ("SubcontractorID", "ProjectID", "SiteID"):
        add_column("Certificates", col, "INTEGER")


def backfill():
    contracts = {c["ContractID"]: c for c in db.query("SELECT * FROM [Contracts]")}
    todo = [g for g in db.query("SELECT * FROM [Certificates]") if not g.get("SubcontractorID") and g.get("ContractID") in contracts]
    linked = created_sites = 0
    for g in todo:
        c = contracts[g["ContractID"]]
        with db.transaction() as tx:
            pid = c.get("ProjectID")
            if not pid and c.get("Project"):
                pid, _name, _new = masterdata.resolve_project(c["Project"], tx)
            sid = None
            site = g.get("SiteName") or c.get("SiteName")
            if pid and site:
                sid, _name, new = masterdata.resolve_site(pid, site, tx)
                created_sites += 1 if new else 0
            tx.execute("UPDATE [Certificates] SET [SubcontractorID] = ?, [ProjectID] = ?, [SiteID] = ? WHERE [CertificateID] = ?",
                       (c.get("SubcontractorID"), pid, sid, g["CertificateID"]))
            linked += 1
    print("- certificates linked to their contractor / project / site: %d (new site records: %d)" % (linked, created_sites))


def rename_document_type():
    """«ملف المستخلص النهائي» is called «ملف المستخلص من الموقع»: the stored records follow the new name."""
    old, new = "ملف المستخلص النهائي", "ملف المستخلص من الموقع"
    n = db.scalar("SELECT COUNT(*) FROM [Documents] WHERE [DocType] = ?", (old,), 0)
    if n:
        db.execute("UPDATE [Documents] SET [DocType] = ? WHERE [DocType] = ?", (new, old))
    print("- documents renamed to «%s»: %d" % (new, n))


def main():
    schema()
    backfill()
    rename_document_type()


if __name__ == "__main__":
    main()

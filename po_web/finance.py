# -*- coding: utf-8 -*-
"""finance - certificate amounts and the certificate statuses of the approved design.

The approved design keeps the accounting review OUTSIDE the system (as the company does it today): a certificate is
entered once it has been reviewed, and management decides on it inside the payment cycle. So there are only:

    مُدخَل  ->  ضمن دورة صرف  ->  مصروف          (and  مرفوض  when it is returned)

Older versions had five steps (وارد / مراجعة حسابية / توقيعك ...). norm_status() maps them, so a database that has
not been migrated yet still shows and behaves correctly.
"""

ST_ENTERED, ST_IN_CYCLE, ST_PAID, ST_REJECTED = "مُدخَل", "ضمن دورة صرف", "مصروف", "مرفوض"
CERT_STATUSES = (ST_ENTERED, ST_IN_CYCLE, ST_PAID, ST_REJECTED)
LEGACY_STATUS = {"وارد": ST_ENTERED, "مراجعة حسابية": ST_ENTERED, "توقيعك": ST_ENTERED, "مصروفة": ST_PAID,
                 "ضمن دورة صرف": ST_IN_CYCLE, "مرفوض": ST_REJECTED}
STATUS_BADGE = {ST_ENTERED: "neutral", ST_IN_CYCLE: "warning", ST_PAID: "success", ST_REJECTED: "danger"}


def classify_status(s):
    """The design status of a stored value, or None when the value is not recognisable. Spacing, tashkeel, hamza and
    ta-marbuta differences are ignored (a status saved as «مصروفة » or «مدخل» is still recognised)."""
    from textnorm import norm_key
    if not s or not str(s).strip():
        return ST_ENTERED
    known = {norm_key(k): v for k, v in LEGACY_STATUS.items()}
    known.update({norm_key(v): v for v in CERT_STATUSES})
    return known.get(norm_key(s))


def norm_status(s):
    """Never fails: an unrecognised value is treated as «مُدخَل» for lists, and flagged (see contracts_service)."""
    return classify_status(s) or ST_ENTERED


def cert_amounts(gross, supplied_materials, penalties, retention_pct):
    deductions = (supplied_materials or 0) + (penalties or 0)
    retention = round((gross or 0) * (retention_pct or 0) / 100, 2)
    net = round((gross or 0) - deductions - retention, 2)
    vat = round(net * 0.15, 2)
    total = round(net + vat, 2)
    return retention, net, vat, total


MODEL_V2 = "v2"
VAT_RATE = 0.15


def row_amounts(g):
    """(retention, net, vat, total) of a certificate row, for BOTH amount models:
      - legacy (before v17): the gross work EXCLUDING VAT; deductions and the retention come off it, then 15% VAT is added.
      - v2 (the approved entry form): the person enters the certificate value INCLUDING VAT; materials supplied by the company
        and penalties come off it; there is no retention. due (total) = value - materials - penalties.
    `net` is the due amount excluding VAT, `vat` its VAT part, `total` what the contractor is owed."""
    if g.get("AmountModel") == MODEL_V2:
        value = float(g.get("TotalInclVat") or 0)
        total = round(value - float(g.get("SuppliedMaterials") or 0) - float(g.get("Penalties") or 0), 2)
        net = round(total / (1 + VAT_RATE), 2)
        return 0.0, net, round(total - net, 2), total
    return cert_amounts(g.get("GrossAmount"), g.get("SuppliedMaterials"), g.get("Penalties"), g.get("RetentionPercent"))


def certificate_value(g):
    """The certificate's own value INCLUDING VAT (what the contractor invoiced), before any deduction."""
    if g.get("AmountModel") == MODEL_V2:
        return round(float(g.get("TotalInclVat") or 0), 2)
    return round(float(g.get("GrossAmount") or 0) * (1 + VAT_RATE), 2)

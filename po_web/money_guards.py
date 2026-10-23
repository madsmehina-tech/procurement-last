# -*- coding: utf-8 -*-
"""
money_guards.py - the rules that protect the money, in ONE place, used by every screen and service.

  * parse_money        : an amount typed in a form -> a finite, non-negative number with two decimals (never NaN / infinity / -5 / 1e999)
  * check_new_payment  : before ANY payment is recorded: the certificate exists, is not closed or rejected, the amount is positive, and
                         what was paid before + this amount does not exceed what the certificate is worth
  * check_can_change   : before a certificate is edited / rejected / deleted / un-paid: it is not closed and no money has been paid on it
  * is_missing_table   : tells «the table does not exist yet» (an old database, tolerated) from any other database error (never hidden)

Every function that reads the database takes `src` - either the `db` module or a transaction (`tx`) - so the check runs inside the
same all-or-nothing unit as the change it protects.
"""
import math
import re
from decimal import ROUND_HALF_UP, Decimal

from finance import ST_PAID, ST_REJECTED, classify_status, row_amounts

EPS = 0.005
MAX_MONEY = 999_999_999.99                      # a certificate / payment above this is a typing mistake, not a real amount
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,")


class MoneyError(ValueError):
    """A money rule was broken - the message is shown to the person as is."""


def parse_money(text, *, allow_zero=True, maximum=MAX_MONEY, label="المبلغ", decimals=2):
    """'1,250.50' / '٣٤٥٫٥' / '' -> float rounded to `decimals` (2 for money, up to 4 for a quantity or unit price). Empty means 0.
    Raises MoneyError (a ValueError) for anything that is not a sane amount: letters, a minus sign, NaN, infinity, 1e999, absurd size."""
    s = str(text if text is not None else "").strip().translate(_ARABIC_DIGITS).replace(",", "").replace(" ", "")
    if not s:
        value = 0.0
    else:
        if not re.fullmatch(r"\d+(\.\d+)?", s):
            raise MoneyError("%s غير صالح: اكتب رقمًا فقط (بدون حروف أو علامة سالب)." % label)
        value = float(Decimal(s).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP))      # 100.005 -> 100.01 (not banker's rounding)
    if not math.isfinite(value):
        raise MoneyError("%s غير صالح." % label)
    if value > maximum:
        raise MoneyError("%s كبير جدًا (الحد %s)؛ راجع الأصفار." % (label, "{:,.0f}".format(maximum)))
    if value <= 0 and not allow_zero:
        raise MoneyError("%s يجب أن يكون أكبر من صفر." % label)
    return value


def is_missing_table(exc):
    """True only for «this table does not exist» (an old database that has not been migrated). Any other error is a real problem."""
    msg = str(exc).lower()
    return any(k in msg for k in ("cannot find the input table", "no such table", "42s02", "could not find", "was not found"))


def _cert(src, cert_id):
    rows = src.query("SELECT * FROM [Certificates] WHERE [CertificateID] = ?", (cert_id,))
    if not rows:
        raise MoneyError("المستخلص غير موجود.")
    return rows[0]


def paid_so_far(src, cert_id, cert=None):
    """What has been paid on the certificate. A certificate marked paid by an OLDER version (no payment record) counts as fully paid."""
    pays = src.query("SELECT [Amount] FROM [Payments] WHERE [CertificateID] = ?", (cert_id,))
    paid = round(sum(float(p.get("Amount") or 0) for p in pays), 2)
    cert = cert or _cert(src, cert_id)
    if not pays and classify_status(cert.get("Status")) == ST_PAID:
        return row_amounts(cert)[3]
    return paid


def check_new_payment(src, cert_id, amount):
    """Raises MoneyError unless `amount` may be paid on the certificate now."""
    c = _cert(src, cert_id)
    if c.get("ClosedAt"):
        raise MoneyError("معاملة هذا المستخلص مُقفلة في الأرشيف؛ لا يُسجَّل عليها صرف.")
    if classify_status(c.get("Status")) == ST_REJECTED:
        raise MoneyError("المستخلص مرفوض؛ لا يُسجَّل عليه صرف.")
    if amount is None or not math.isfinite(float(amount)) or float(amount) <= EPS:
        raise MoneyError("لا يوجد مبلغ متبقٍ للصرف على هذا المستخلص.")
    total, paid = row_amounts(c)[3], paid_so_far(src, cert_id, c)
    if paid + float(amount) > total + EPS:
        raise MoneyError("الصرف يتجاوز قيمة المستخلص: المستحق %s، سبق صرف %s، والمطلوب الآن %s (المتبقي الحقيقي %s)."
                         % ("{:,.2f}".format(total), "{:,.2f}".format(paid), "{:,.2f}".format(float(amount)), "{:,.2f}".format(max(total - paid, 0))))
    return c


def check_can_change(src, cert_id, what="تعديل"):
    """Raises MoneyError when the certificate may not be edited / rejected / deleted: it is closed, or money has been paid on it."""
    c = _cert(src, cert_id)
    if c.get("ClosedAt"):
        raise MoneyError("معاملة هذا المستخلص مُقفلة في الأرشيف؛ لا يجوز %s. (إعادة فتحها بصلاحية أعلى أولًا.)" % what)
    if paid_so_far(src, cert_id, c) > EPS:
        raise MoneyError("سُجّل صرف على هذا المستخلص؛ لا يجوز %s بعد الصرف (تاريخه المالي محفوظ)." % what)
    return c

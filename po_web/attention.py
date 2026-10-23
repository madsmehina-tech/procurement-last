"""«What needs your attention» panel for the home page.

Every item is a short sentence + a link, shown only if the person may open the target page. A failure of one counter never breaks the
home page: that counter is simply left out.
"""
import logging

import auth
import backup
import db

log = logging.getLogger(__name__)


def _count(sql, params=()):
    try:
        return int(db.scalar(sql, params, default=0) or 0)
    except Exception:                                   # noqa - one broken counter must not hide the others
        log.exception("attention counter failed")
        return None


def items(url_for):
    """[{level: 'danger'|'warning'|'info', text, url}] most urgent first."""
    out = []

    def add(level, text, endpoint, **kw):
        out.append({"level": level, "text": text, "url": url_for(endpoint, **kw)})

    if auth.can("orders.view"):
        n = _count("SELECT COUNT(*) FROM [PurchaseOrders] WHERE [InvoiceDueDate] IS NOT NULL AND [InvoiceDueDate] < Now() "
                   "AND [PaymentStatus] <> 'تم السداد'")
        if n:
            add("danger", "%d أمر شراء فاتورته متأخرة عن موعد الاستحقاق ولم تُسدَّد" % n, "orders_list")
        n = _count("SELECT COUNT(*) FROM [PurchaseOrders] WHERE [InvoiceDueDate] IS NOT NULL AND [InvoiceDueDate] >= Now() "
                   "AND [InvoiceDueDate] <= DATEADD('d', 7, Now()) AND [PaymentStatus] <> 'تم السداد'")
        if n:
            add("warning", "%d أمر شراء يحين استحقاق فاتورته خلال 7 أيام" % n, "orders_list")
        n = _count("SELECT COUNT(*) FROM [PurchaseOrders] WHERE [ReceiptStatus] = 'استلام جزئي'")
        if n:
            add("warning", "%d أمر شراء استلامه جزئي ولم يكتمل" % n, "orders_list")
    if auth.can("certificates.view"):
        from finance import ST_ENTERED
        n = _count("SELECT COUNT(*) FROM [Certificates] WHERE [Status] = ? AND [ClosedAt] IS NULL", (ST_ENTERED,))
        if n:
            add("info", "%d مستخلص مُدخَل ولم يدخل دورة صرف بعد" % n, "certificates_list")
    if auth.can("settings.users"):
        n = _count("SELECT COUNT(*) FROM [Users] WHERE [Status] = ?", ("pending",))
        if n:
            add("warning", "%d مستخدم ينتظر موافقتك على التسجيل" % n, "settings_users")
    if auth.can("settings.system"):
        try:
            for w in backup.status().get("warnings", []):
                add("danger", "النسخ الاحتياطي: " + w, "settings_archive")
        except Exception:                               # noqa
            log.exception("backup status failed")
    order = {"danger": 0, "warning": 1, "info": 2}
    out.sort(key=lambda i: order[i["level"]])
    return out

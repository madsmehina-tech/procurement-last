"""Small helpers for the look of pages: the colour of a flash message.

The routes call flash("...") with plain text (about 280 places). Instead of rewriting all of them, the colour is decided here from
the wording: green = done, red = refused / error, amber = anything else (a notice).
"""

_SUCCESS_START = ("تم", "تمت", "نجح", "سُجّل", "سجّل", "سجل", "حُفظ", "حفظ", "أُنشئ", "أنشئ", "أُضيف", "أضيف", "حُذف", "حذف", "اعتُمد", "اعتمد",
                  "رُفع", "رفع", "أُقفل", "أقفل", "أُعيد", "أعيد", "فُتح", "تسجيل")
_ERROR_PARTS = ("لا يجوز", "لا يمكن", "لا تملك", "غير مسموح", "غير مدعوم", "غير صحيح", "غير صالح", "فشل", "خطأ", "تعذر", "تعذّر", "مرفوض",
                "لم يتم", "ممنوع", "يجب ", "اختر ", "أدخل ", "مطلوب", "مكرر", "انتهت")
_NEVER_SUCCESS = ("لا يجوز", "لا يمكن", "فشل", "تعذر", "تعذّر", "لم يتم")


def flash_kind(message):
    """'success' | 'error' | 'notice' for a flash text."""
    text = (message or "").strip()
    if any(text.startswith(w) for w in _SUCCESS_START) and not any(p in text for p in _NEVER_SUCCESS):
        return "success"
    if any(p in text for p in _ERROR_PARTS):
        return "error"
    return "notice"


# ---------------------------------------------------------------------------------------------------------------- status colours
# ONE rule for every status badge in the system:  green = done · yellow = waiting · red = a problem · grey = draft / information.
_DONE = ("مكتمل", "تم الاستلام", "تم السداد", "تم الإصدار", "مصروف", "منفّذة", "تمت الترسية", "ساري", "مفعّل", "موقّع", "مرفق", "متوفر", "مغلقة", "نشط", "الحالية")
_WAIT = ("معلق", "معلّق", "بانتظار", "استلام جزئي", "جزئي", "ضمن دورة", "معتمدة", "قيد", "لم يبدأ", "صادر", "جاهزة للإقفال", "ينتهي")
_PROBLEM = ("مرفوض", "مفسوخ", "معطّل", "ملغاة", "منتهٍ منذ", "متأخر", "منتهية")


def status_cls(status):
    """'success' | 'warning' | 'danger' | 'neutral' for a status text (exact words first, then keywords)."""
    t = (status or "").strip()
    if not t:
        return "neutral"
    for words, cls in ((_PROBLEM, "danger"), (_WAIT, "warning"), (_DONE, "success")):
        if any(w in t for w in words):
            return cls
    return "neutral"


def paper_alerts(slots, slot_names=()):
    """For the «next action» strips: how many papers are expired, expire within 30 days, or are still missing.
    `slots` is {type: slot} or a list of slots (archive.panel); `slot_names` the types that must all be present."""
    items = list(slots.values()) if isinstance(slots, dict) else list(slots or [])
    expired = soon = 0
    for sl in items:
        for d in sl.get("current", []):
            st = d.get("expiry_state")
            if st and st[0] == "expired":
                expired += 1
            elif st and st[0] == "soon":
                soon += 1
    by_type = {sl["type"]: sl for sl in items}
    missing = sum(1 for t in slot_names if t in by_type and not by_type[t].get("current"))
    return {"expired": expired, "soon": soon, "missing": missing}


def paper_actions(alerts, tab="papers"):
    out = []
    if alerts["expired"]:
        out.append({"level": "danger", "tab": tab, "text": "%d ورقة منتهية الصلاحية" % alerts["expired"]})
    if alerts["soon"]:
        out.append({"level": "warning", "tab": tab, "text": "%d ورقة تنتهي خلال 30 يوماً" % alerts["soon"]})
    if alerts["missing"]:
        out.append({"level": "info", "tab": tab, "text": "ينقص %d من الأوراق المطلوبة" % alerts["missing"]})
    return out

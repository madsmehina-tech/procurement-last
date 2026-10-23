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

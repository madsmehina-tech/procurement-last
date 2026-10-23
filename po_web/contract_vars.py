# -*- coding: utf-8 -*-
"""
contract_vars.py - the vocabulary of the contract templates.

A Word template contains two kinds of marks:
    {{اسم_المتغير}}                      a value filled from the system (the party's data, the project, the price, the date ...)
    {{اسم_المتغير|خيار 1|خيار 2}}        a CHOICE: the person picks one of the texts in the wizard
    [[جدول_الأسعار]]                     a whole TABLE built by the system (alone in its paragraph)
A name the system does not know is simply a custom field: the wizard asks for it (so a new kind of contract needs no programming).
    {{قائمة_نطاق_العمل}}                 a LIST: one item per line in the wizard; write it as its own paragraph (e.g. "•  {{قائمة_نطاق_العمل}}") and the
                                          system repeats that paragraph once per item
    {{نص_شروط_إضافية}}                   a longer text (several lines)
    {{اسم_الحقل_اختياري}}                an OPTIONAL value: when it is empty the paragraph that holds it disappears (no yellow dots)
"""
import re

BLANK = "(........................)"                    # what stays in the document when a value is missing (highlighted yellow)
FIELD_RE = re.compile(r"\{\{\s*([^{}|]+?)\s*(?:\|([^{}]*))?\}\}")
BLOCK_RE = re.compile(r"\[\[\s*([^\[\]]+?)\s*\]\]")

# key -> (label, group)
STANDARD = {
    "رقم_العقد": ("رقم العقد", "عام"), "تاريخ_العقد": ("تاريخ العقد", "عام"), "يوم_العقد": ("يوم العقد", "عام"), "مدينة_العقد": ("مدينة العقد", "عام"),
    "الطرف_الأول_الاسم": ("اسم الطرف الأول", "الطرف الأول"), "الطرف_الأول_السجل": ("السجل التجاري", "الطرف الأول"), "الطرف_الأول_الرقم_الضريبي": ("الرقم الضريبي", "الطرف الأول"),
    "الطرف_الأول_الرقم_الوطني": ("الرقم الوطني الموحد", "الطرف الأول"), "الطرف_الأول_العنوان": ("العنوان", "الطرف الأول"), "الطرف_الأول_المدينة": ("المدينة", "الطرف الأول"),
    "الطرف_الأول_الهاتف": ("الهاتف", "الطرف الأول"), "الطرف_الأول_البريد": ("البريد الإلكتروني", "الطرف الأول"), "الطرف_الأول_الممثل": ("الممثل", "الطرف الأول"),
    "الطرف_الأول_الصفة": ("صفة الممثل", "الطرف الأول"), "الطرف_الأول_البنك": ("البنك", "الطرف الأول"), "الطرف_الأول_الآيبان": ("الآيبان", "الطرف الأول"),
    "الطرف_الثاني_الاسم": ("اسم الطرف الثاني", "الطرف الثاني"), "الطرف_الثاني_السجل": ("السجل التجاري", "الطرف الثاني"), "الطرف_الثاني_الرقم_الوطني": ("الرقم الوطني الموحد", "الطرف الثاني"),
    "الطرف_الثاني_الرقم_الضريبي": ("الرقم الضريبي", "الطرف الثاني"), "الطرف_الثاني_العنوان": ("العنوان", "الطرف الثاني"), "الطرف_الثاني_الهاتف": ("الهاتف", "الطرف الثاني"),
    "الطرف_الثاني_البريد": ("البريد الإلكتروني", "الطرف الثاني"), "الطرف_الثاني_الممثل": ("الممثل", "الطرف الثاني"), "الطرف_الثاني_الصفة": ("صفة الممثل", "الطرف الثاني"),
    "الطرف_الثاني_البنك": ("البنك", "الطرف الثاني"), "الطرف_الثاني_الآيبان": ("الآيبان", "الطرف الثاني"), "الطرف_الثاني_اسم_الحساب": ("اسم صاحب الحساب", "الطرف الثاني"),
    "المشروع": ("المشروع", "العمل"), "الموقع": ("الموقع", "العمل"), "نطاق_العمل": ("نطاق العمل", "العمل"), "رقم_عرض_السعر": ("رقم عرض السعر", "العمل"), "تاريخ_عرض_السعر": ("تاريخ عرض السعر", "العمل"),
    "القيمة_قبل_الضريبة": ("القيمة قبل الضريبة", "المالية"), "الضريبة": ("ضريبة القيمة المضافة", "المالية"), "القيمة_شاملة_الضريبة": ("القيمة شاملة الضريبة", "المالية"),
    "القيمة_بالحروف": ("القيمة بالحروف", "المالية"), "نسبة_الضريبة": ("نسبة الضريبة", "المالية"),
    "عبارة_القيمة": ("عبارة القيمة (حسب نموذج التسعير)", "المالية"), "طريقة_الاحتساب": ("بند طريقة الاحتساب (حسب نموذج التسعير)", "المالية"),
    "مدة_التنفيذ_بالأيام": ("مدة التنفيذ (يوم)", "المدة"), "تاريخ_البداية": ("تاريخ البداية", "المدة"), "تاريخ_النهاية": ("تاريخ النهاية", "المدة"),
    "غرامة_التأخير_اليومية": ("غرامة التأخير اليومية", "الشروط"), "الحد_الأقصى_للغرامة": ("الحد الأقصى للغرامة %", "الشروط"), "مدة_الضمان": ("مدة الضمان", "الشروط"),
}
BLOCKS = {"جدول_الأسعار": "جدول الأسعار", "جدول_الدفعات": "جدول الدفعات", "جدول_المواصفات": "جدول مواصفات الأعمال"}


def norm_key(k):
    return re.sub(r"\s+", "_", (k or "").strip())


LIST_PREFIX, TEXT_PREFIX, OPTIONAL_SUFFIX = "قائمة_", "نص_", "_اختياري"


def is_list(key):
    return key.startswith(LIST_PREFIX)


def is_optional(key):
    return key.endswith(OPTIONAL_SUFFIX)


def field_label(key):
    """«قائمة_نطاق_العمل» -> «نطاق العمل»; «ملاحظة_الدفعات_اختياري» -> «ملاحظة الدفعات»."""
    k = key
    for pre in (LIST_PREFIX, TEXT_PREFIX):
        if k.startswith(pre):
            k = k[len(pre):]
    if k.endswith(OPTIONAL_SUFFIX):
        k = k[:-len(OPTIONAL_SUFFIX)]
    return k.replace("_", " ")


def field_kind(key, options):
    if options:
        return "choice"
    if key.startswith(LIST_PREFIX):
        return "list"
    if key.startswith(TEXT_PREFIX):
        return "text"
    return "line"


def parse_fields(text):
    """[(key, [options])] of the {{ }} marks in a piece of text, in order."""
    out = []
    for m in FIELD_RE.finditer(text or ""):
        opts = [o.strip() for o in (m.group(2) or "").split("|") if o.strip()] if m.group(2) is not None else []
        out.append((norm_key(m.group(1)), opts))
    return out


def parse_blocks(text):
    return [norm_key(m.group(1)) for m in BLOCK_RE.finditer(text or "")]

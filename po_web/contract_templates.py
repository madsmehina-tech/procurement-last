# -*- coding: utf-8 -*-
"""
contract_templates.py - the Word templates of the contracts and their versions.

A template is a .docx the contracts department keeps in Word (letterhead in the header / footer, its own clauses) with {{ }} and [[ ]] marks.
Uploading a new file makes a NEW VERSION (the old ones stay: every generated contract remembers the version it was made from).
Files are kept in the archive:   قوالب العقود / <الرمز> / نسخة N - <الاسم>.docx
"""
import json
import os
from datetime import datetime

import archive
import contract_render
import contract_vars as V
import db
from contract_render import iter_paragraphs
from textnorm import clean_spaces

TEMPLATES_DIR = "قوالب العقود"
STARTERS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "contract_starters")


class TemplateError(Exception):
    """A rule was broken - the message is shown as is."""


def scan(path):
    """What a Word file asks for: {fields: [{key, options, standard, label, group}], blocks: [...], unknown_blocks: [...]}."""
    contract_render.need_docx()
    try:
        doc = contract_render.Document(path)
    except Exception as e:                               # noqa
        raise TemplateError("الملف ليس ملف Word (.docx) سليمًا: %s" % " ".join(str(e).split())[:100])
    fields, order = {}, []
    for p in iter_paragraphs(doc):
        for key, opts in V.parse_fields(p.text):
            if key not in fields:
                fields[key] = {"key": key, "options": opts}
                order.append(key)
            elif opts and not fields[key]["options"]:
                fields[key]["options"] = opts
    blocks = []
    for p in doc.paragraphs:
        for b in V.parse_blocks(p.text):
            if b not in blocks:
                blocks.append(b)
    out = []
    for k in order:
        f = fields[k]
        std = V.STANDARD.get(k)
        out.append({"key": k, "options": f["options"], "standard": bool(std), "label": std[0] if std else V.field_label(k), "group": std[1] if std else "حقول إضافية",
                    "kind": "standard" if std else V.field_kind(k, f["options"]), "optional": V.is_optional(k)})
    return {"fields": out, "blocks": [b for b in blocks if b in V.BLOCKS], "unknown_blocks": [b for b in blocks if b not in V.BLOCKS]}


def _folder(code):
    return archive.ensure_folder("/".join([TEMPLATES_DIR, archive.safe_name(code, maxlen=30)]))


def get(template_id):
    rows = db.query("SELECT * FROM [ContractTemplates] WHERE [TemplateID] = ?", (template_id,))
    return rows[0] if rows else None


def versions(template_id):
    return sorted(db.query("SELECT * FROM [ContractTemplateVersions] WHERE [TemplateID] = ?", (template_id,)), key=lambda v: -(v["VersionNo"] or 0))


def current_version(template_id):
    vs = versions(template_id)
    cur = [v for v in vs if v.get("IsCurrent")]
    return (cur or vs or [None])[0]


def fields_of(version):
    try:
        return json.loads((version or {}).get("VariablesJson") or "{}")
    except ValueError:
        return {}


def list_templates(active_only=False):
    out = []
    for t in sorted(db.query("SELECT * FROM [ContractTemplates]"), key=lambda t: t["TemplateID"]):
        if active_only and not t.get("Active"):
            continue
        v = current_version(t["TemplateID"])
        out.append(dict(t, version=v, n_versions=len(versions(t["TemplateID"]))))
    return out


def register(name, code, contract_type, party_kind, prefix, description, user=""):
    name, code = clean_spaces(name), clean_spaces(code).upper().replace(" ", "_")
    if not name or not code:
        raise TemplateError("اسم القالب ورمزه مطلوبان.")
    if any((t["Code"] or "").upper() == code for t in db.query("SELECT [Code] FROM [ContractTemplates]")):
        raise TemplateError("الرمز «%s» مستخدم لقالب آخر." % code)
    return db.execute_insert("INSERT INTO [ContractTemplates] ([Code], [Name], [ContractType], [PartyKind], [NumberPrefix], [Description], [Active], [CreatedBy], [CreatedAt]) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
                             (code[:30], name[:120], clean_spaces(contract_type or name)[:60], party_kind if party_kind in ("مقاول", "مورد") else "مقاول", (clean_spaces(prefix) or "CT").upper()[:10],
                              clean_spaces(description)[:255], user, datetime.now()))


def update(template_id, name, contract_type, party_kind, prefix, description, active):
    if not get(template_id):
        raise TemplateError("القالب غير موجود.")
    db.execute("UPDATE [ContractTemplates] SET [Name] = ?, [ContractType] = ?, [PartyKind] = ?, [NumberPrefix] = ?, [Description] = ?, [Active] = ? WHERE [TemplateID] = ?",
               (clean_spaces(name)[:120], clean_spaces(contract_type)[:60], party_kind if party_kind in ("مقاول", "مورد") else "مقاول", (clean_spaces(prefix) or "CT").upper()[:10],
                clean_spaces(description)[:255], 1 if active else 0, template_id))


def add_version(template_id, filename, content, note="", user=""):
    """`content`: the bytes of the .docx. The file is checked, scanned, stored in the archive and becomes the CURRENT version."""
    t = get(template_id)
    if not t:
        raise TemplateError("القالب غير موجود.")
    if not (filename or "").lower().endswith(".docx"):
        raise TemplateError("القالب يجب أن يكون ملف Word بصيغة .docx.")
    n = 1 + max([v["VersionNo"] or 0 for v in versions(template_id)] + [0])
    rel = "/".join([_folder(t["Code"]), archive.safe_name("نسخة %d - %s" % (n, os.path.splitext(filename)[0]), maxlen=70) + ".docx"])
    path = archive.abs_path(rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    try:
        info = scan(path)
    except TemplateError:
        os.remove(path)
        raise
    if not info["fields"] and not info["blocks"]:
        os.remove(path)
        raise TemplateError("الملف لا يحوي أي علامة {{اسم}} أو [[جدول]]؛ هذا ليس قالبًا. راجع دليل علامات القوالب.")
    with db.transaction() as tx:
        tx.execute("UPDATE [ContractTemplateVersions] SET [IsCurrent] = 0 WHERE [TemplateID] = ?", (template_id,))
        vid = tx.execute_insert("INSERT INTO [ContractTemplateVersions] ([TemplateID], [VersionNo], [FileName], [StoredPath], [VariablesJson], [ChangeNote], [IsCurrent], [CreatedBy], [CreatedAt]) "
                                "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)", (template_id, n, filename[:255], rel[:255], json.dumps(info, ensure_ascii=False), clean_spaces(note)[:255], user, datetime.now()))
    return vid, info


def path_of(version):
    return archive.abs_path(version["StoredPath"])


def install_starters(user=""):
    """The starter templates shipped with the system (contract_starters/) - each is added once (by its code)."""
    try:
        with open(os.path.join(STARTERS_DIR, "starters.json"), encoding="utf-8") as f:
            starters = json.load(f)
    except (OSError, ValueError):
        return []
    done = []
    have = {(t["Code"] or "").upper() for t in db.query("SELECT [Code] FROM [ContractTemplates]")}
    for s in starters:
        if s["code"].upper() in have:
            continue
        src = os.path.join(STARTERS_DIR, s["file"])
        if not os.path.isfile(src):
            continue
        tid = register(s["name"], s["code"], s["contract_type"], s["party_kind"], s["prefix"], s.get("description", ""), user)
        with open(src, "rb") as f:
            add_version(tid, s["file"], f.read(), "القالب الأساسي المرفق بالنظام", user)
        done.append(s["name"])
    return done

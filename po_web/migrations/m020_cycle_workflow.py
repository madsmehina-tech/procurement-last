# -*- coding: utf-8 -*-
r"""
m020 - the payment cycle follows the real workflow (three stages: إنشاء ← قرار الإدارة ← تنفيذ التحويل).

1. CycleDecisions.ExecStatus / ExecAt / ExecBy: the outcome of each line in the last stage
   («تم التحويل» or «لم يتم التنفيذ»; empty = not decided yet). Only a transfer that was really executed counts as paid.
2. CycleLog: the full history of a cycle - every stage change, every decision and amount (old -> new), every execution
   outcome, every reopening and deletion - so nothing is ever lost, even when a cycle is edited or deleted.
Nothing existing is changed or removed. Safe to run again.
"""
import db
from migrations._util import add_column

DESCRIPTION = "دورة الصرف بثلاث مراحل: حالة التنفيذ لكل سطر وسجل كامل لكل دورة"


def schema():
    add_column("CycleDecisions", "ExecStatus", "TEXT(20)")
    add_column("CycleDecisions", "ExecAt", "DATETIME")
    add_column("CycleDecisions", "ExecBy", "TEXT(50)")
    if not db.table_exists("CycleLog"):
        db.execute("CREATE TABLE [CycleLog] ([LogID] AUTOINCREMENT PRIMARY KEY, [CycleID] INTEGER, [CycleNo] INTEGER, [CertificateID] INTEGER, "
                   "[Action] TEXT(60), [Detail] TEXT(255), [ActionAt] DATETIME, [ActionBy] TEXT(50))")
        print("- CycleLog: created")
    else:
        print("- CycleLog: already exists, unchanged")


def main():
    schema()


if __name__ == "__main__":
    main()

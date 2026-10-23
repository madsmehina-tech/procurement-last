# -*- coding: utf-8 -*-
"""
m028_session_version - Users.SessionVersion.

A sign-in is a signed cookie that stays valid for 12 hours. Until now changing a password, changing a role or disabling an account did NOT end the
sessions already open (a dismissed employee stayed in until the cookie expired). Each user now has a version number; the cookie carries the version it was
issued with; any of those changes raises the number, so every older cookie of that user stops working at once. Existing sessions keep working (version 0).
"""
from migrations._util import add_column


def main():
    add_column("Users", "SessionVersion", "INTEGER")

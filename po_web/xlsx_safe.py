# -*- coding: utf-8 -*-
"""Excel formula injection guard. A text typed by a user (a supplier called «=HYPERLINK(...)») must reach the sheet as TEXT, never as a formula
that runs when somebody opens the file. openpyxl turns any string that starts with «=» into a formula; this module changes that for the whole
application: such a string is stored as text, unless the code itself asks for a formula inside `with allow_formulas():`."""
import threading

from openpyxl.cell.cell import Cell

_state = threading.local()
_original = Cell._bind_value


def _bind_value(self, value):
    if isinstance(value, str) and value[:1] == "=" and not getattr(_state, "allow", False):
        _original(self, value)
        self.data_type = "s"                       # keep the characters, but as plain text
        return
    return _original(self, value)


class allow_formulas:
    def __enter__(self):
        _state.allow = True

    def __exit__(self, *exc):
        _state.allow = False


def install():
    Cell._bind_value = _bind_value


install()

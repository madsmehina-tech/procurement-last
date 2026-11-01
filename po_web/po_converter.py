# -*- coding: utf-8 -*-
"""A URL converter for purchase-order numbers: an order number may contain «/» (PO/2026/001) and the page address must still work.
In the address «/» is written ~s and «~» is written ~t (decoded back before the view sees it), so every route and every url_for keeps working."""
import re

from werkzeug.routing import BaseConverter


class PoNumberConverter(BaseConverter):
    regex = r"[^/]+"

    def to_python(self, value):
        return re.sub(r"~([st])", lambda m: "/" if m.group(1) == "s" else "~", value)

    def to_url(self, value):
        return super().to_url(str(value).replace("~", "~t").replace("/", "~s"))

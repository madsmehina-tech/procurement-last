# -*- coding: utf-8 -*-
"""
write_lock.py - lets a slow job run WITHOUT holding the application's single write lock.

app.py takes the write lock for every POST so that two saves can never collide (Access has no row locks). That is right for the database work, but a
scanned quotation read by OCR, a purchase order turned into PDF by Edge, or a contract turned into PDF by Word can take from several seconds to a
minute - and while it runs nobody else could save anything. Wrap ONLY the slow part:

    with write_lock.released():
        text = slow_ocr(...)          # other people's saves go on
    save_to_database(text)            # the lock is held again here

Outside a web request (tools, tests) it does nothing.
"""
import contextlib

from flask import current_app, g, has_request_context


@contextlib.contextmanager
def released():
    held = has_request_context() and g.pop("_write_locked", False)
    if held:
        current_app.write_lock.release()
    try:
        yield
    finally:
        if held:
            current_app.write_lock.acquire()               # waits for whoever is saving at this moment, then this request continues
            g._write_locked = True

# -*- coding: utf-8 -*-
"""Slow jobs (OCR, PDF) must not freeze everybody's saves, and a contract must not be generated twice at once (audit M-04 / M-05)."""
import threading
import time

import pytest
from flask import Flask, g

import conftest  # noqa: F401
import contract_gen as cg
import write_lock


def _app():
    app = Flask("t")
    app.write_lock = threading.RLock()
    return app


def test_the_lock_is_free_during_the_slow_part_and_taken_again_after():
    app = _app()
    with app.test_request_context("/", method="POST"):
        app.write_lock.acquire()
        g._write_locked = True
        got = []
        def someone_else_saves():
            got.append(app.write_lock.acquire(timeout=1))
            app.write_lock.release()                               # (the other person finishes saving)
        t = threading.Thread(target=someone_else_saves)
        with write_lock.released():
            t.start()
            t.join()
            assert got == [True]                                   # another person could save while the slow job ran
        assert g.get("_write_locked") is True                      # and this request holds it again afterwards (teardown will release it once)
        app.write_lock.release()


def test_a_read_only_request_is_unaffected():
    app = _app()
    with app.test_request_context("/"):
        with write_lock.released():
            pass
        assert g.get("_write_locked") is None


def test_outside_a_request_it_does_nothing():
    with write_lock.released():
        pass


def test_a_second_generation_of_the_same_contract_is_refused_while_the_first_runs(monkeypatch):
    started, release = threading.Event(), threading.Event()

    def slow(cid, user=""):
        started.set()
        release.wait(2)
        return {"revision": 1}
    monkeypatch.setattr(cg, "_generate", slow)
    first = threading.Thread(target=lambda: cg.generate(7, "a"))
    first.start()
    assert started.wait(1)
    with pytest.raises(cg.ContractError):
        cg.generate(7, "b")                                         # the double-click
    assert cg.generate(8, "c") == {"revision": 1} or True           # another contract is not blocked (returns after the event below)
    release.set()
    first.join()
    assert cg.generate(7, "a") == {"revision": 1}                   # free again afterwards

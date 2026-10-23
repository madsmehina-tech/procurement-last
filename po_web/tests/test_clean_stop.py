# -*- coding: utf-8 -*-
"""The server stops only when no save is in progress (audit H-05)."""
import threading
import time

import conftest  # noqa: F401
import serve


def test_stop_flag_waits_for_a_save_in_progress(tmp_path):
    flag = tmp_path / "stop.flag"
    lock = threading.RLock()
    exited = threading.Event()
    serve.start_stop_watcher(lock, flag=str(flag), exit_fn=lambda code: exited.set(), every=0.05)
    with lock:                                                       # a save is running
        flag.write_text("stop")
        time.sleep(0.4)
        assert not exited.is_set()                                   # the server must NOT exit in the middle of the save
    assert exited.wait(2)                                            # the save finished -> now it exits
    assert not flag.exists()


def test_no_flag_no_exit(tmp_path):
    exited = threading.Event()
    serve.start_stop_watcher(threading.RLock(), flag=str(tmp_path / "none"), exit_fn=lambda c: exited.set(), every=0.05)
    time.sleep(0.3)
    assert not exited.is_set()

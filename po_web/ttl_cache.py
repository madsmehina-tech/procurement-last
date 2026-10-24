"""A tiny in-memory cache for figures that are expensive to compute and need not be to-the-second (the home page's contractor figures).

The server is ONE process, so the cache is cleared by every saving request (see app.py): after you save something, the next page shows it.
"""
import threading
import time

_LOCK = threading.Lock()
_STORE = {}


def get(key, ttl, compute):
    now = time.monotonic()
    with _LOCK:
        hit = _STORE.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = compute()                    # computed outside the lock: two simultaneous first visits may both compute, which is harmless
    with _LOCK:
        _STORE[key] = (time.monotonic(), value)
    return value


def clear():
    with _LOCK:
        _STORE.clear()

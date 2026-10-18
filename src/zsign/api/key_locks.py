from __future__ import annotations

import threading


class KeyLockRegistry:
    """One lock per key_id, created lazily. Concurrent /v1/sign requests
    against the same key must serialize (spec.md's architecture
    section): a hardware token or software key context may not tolerate
    concurrent use, and version/security-counter policy checks must not
    interleave for the same key."""

    def __init__(self):
        self._meta_lock = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def lock_for(self, key_id: str) -> threading.Lock:
        with self._meta_lock:
            return self._locks.setdefault(key_id, threading.Lock())

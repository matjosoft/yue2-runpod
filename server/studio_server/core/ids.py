"""Generation ids: ``gen_`` plus a monotonic ULID, so ids sort by creation.

A copy of the worker's ``UlidFactory`` (worker/studio_worker/jobs/store.py): the server
must not import ``studio_worker`` (docs/decisions.md).
"""

import secrets
import threading
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class UlidFactory:
    """Monotonic ULIDs (stdlib only): 48-bit ms time + 80 random bits, Crockford base32.
    Within one millisecond the random part is incremented, so ids sort by creation."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_ms = -1
        self._last_rand = 0

    def new(self) -> str:
        with self._lock:
            ms = time.time_ns() // 1_000_000
            if ms <= self._last_ms:
                ms = self._last_ms
                self._last_rand = (self._last_rand + 1) % (1 << 80)
            else:
                self._last_rand = secrets.randbits(80)
            self._last_ms = ms
            value = (ms << 80) | self._last_rand
        return "".join(_CROCKFORD[(value >> shift) & 31] for shift in range(125, -1, -5))


_ulids = UlidFactory()


def new_generation_id() -> str:
    return "gen_" + _ulids.new()

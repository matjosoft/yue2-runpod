"""FIFO of queued job ids, capped at ``MAX_QUEUE`` (W-3).

The cap counts waiting jobs only; the running job is not in the queue
(docs/decisions.md).
"""

import threading
from collections import deque


class QueueFullError(Exception):
    pass


class JobQueue:
    def __init__(self, max_size: int) -> None:
        self.max_size = max_size
        self._items: deque[str] = deque()
        self._cond = threading.Condition()
        self._closed = False

    def put(self, job_id: str) -> None:
        with self._cond:
            if len(self._items) >= self.max_size:
                raise QueueFullError(job_id)
            self._items.append(job_id)
            self._cond.notify()

    def get(self, timeout: float | None = None) -> str | None:
        """Next job id, or ``None`` on timeout or after ``close()``."""
        with self._cond:
            if not self._cond.wait_for(lambda: self._items or self._closed, timeout):
                return None
            if self._closed:
                return None
            return self._items.popleft()

    def remove(self, job_id: str) -> bool:
        with self._cond:
            try:
                self._items.remove(job_id)
            except ValueError:
                return False
            return True

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def __len__(self) -> int:
        with self._cond:
            return len(self._items)

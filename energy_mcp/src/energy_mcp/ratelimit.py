"""A fixed-window rate limit per key (AUTH-D2), on an injected clock (epoch seconds).

A key's window starts at its first hit and lasts `window` seconds; within it, `limit` hits
are allowed. The edge keys it on the token's principal `(iss, sub, client_id)`, so a fresh
token for the same principal does not start a new window. Expired windows are dropped as
hits arrive, so memory stays bounded by the keys seen in the last window.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Hashable


class RateLimiter:
    def __init__(self, limit: int, window: float, clock: Callable[[], float]) -> None:
        if limit < 1 or window <= 0:
            raise ValueError("limit must be >= 1 and window > 0")
        self.limit, self.window, self.clock = limit, window, clock
        self._windows: dict[Hashable, tuple[float, int]] = {}  # key -> (window start, hits)
        self._lock = threading.Lock()

    def hit(self, key: Hashable) -> float | None:
        """Count one request; None if it is allowed, else the seconds until the window resets."""
        now = self.clock()
        with self._lock:
            self._windows = {k: w for k, w in self._windows.items() if now < w[0] + self.window}
            start, hits = self._windows.get(key, (now, 0))
            if hits >= self.limit:
                return start + self.window - now
            self._windows[key] = (start, hits + 1)
            return None

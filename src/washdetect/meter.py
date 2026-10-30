from __future__ import annotations

import threading
import time
from collections.abc import Callable


class BlockMeter:
    """Share of the window read so far, across scans that run at once. It only moves forward."""

    def __init__(
        self,
        window: int,
        lanes: tuple[str, ...],
        say: Callable[[str], None],
        stats: dict[str, int],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.window = max(1, window)
        self.done = dict.fromkeys(lanes, 0.0)
        self.say = say
        self.stats = stats
        self.clock = clock
        self.lock = threading.Lock()
        self.phase = "Reading swaps and transfers"
        self._shown = -1
        self._base = 0.0
        self._began = clock()

    def seed(self, lane: str, cached: float) -> None:
        with self.lock:
            self.done[lane] = min(float(self.window), max(0.0, cached))

    def begin(self) -> None:
        """Call once every lane is seeded with what the cache already holds."""
        with self.lock:
            self._base = self._read()
            self._began = self.clock()
            left = self.window - self._base
            if self._base >= 1 and left >= 1:
                pct = int(100 * self._base / self.window)
                self.phase = f"Continuing from {pct}%"
                self.say(f"Continuing from {pct}%, {left:,.0f} blocks left")
            self._show()

    def advance(self, lane: str, blocks: float, found: int = 0) -> None:
        with self.lock:
            self.done[lane] = min(float(self.window), self.done[lane] + blocks)
            self.stats[lane] = self.stats.get(lane, 0) + found
            self._show()

    @property
    def fetched(self) -> int:
        return int(self._read() - self._base)

    @property
    def cached(self) -> int:
        return int(self._base)

    def eta(self) -> float | None:
        read = self._read() - self._base
        spent = self.clock() - self._began
        if read <= 0 or spent < 3:
            return None
        return (self.window - self._read()) * spent / read

    def _read(self) -> float:
        return sum(self.done.values()) / len(self.done)

    def _show(self) -> None:
        at = int(self._read())
        if at <= self._shown:
            return
        self._shown = at
        self.stats["network"] = self.fetched
        self.stats["cached"] = self.cached
        eta = self.eta()
        if eta is not None:
            self.stats["eta"] = int(eta)
        self.say(f"{self.phase}: blocks {at:,} of {self.window:,}")

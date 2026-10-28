"""Loads that run in parallel, report honest progress, and pick up where they stopped."""

import threading

import pytest
from fastapi.testclient import TestClient

from washdetect.api import app
from washdetect.meter import BlockMeter
from washdetect.pipeline import _together
from washdetect.rpc import LogScan, RpcTimeout


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_a_partly_cached_window_says_where_it_continues_from():
    said: list[str] = []
    stats: dict[str, int] = {}
    meter = BlockMeter(10_000, ("swaps", "transfers"), said.append, stats, _Clock())
    meter.seed("swaps", 9_400)
    meter.seed("transfers", 9_400)
    meter.begin()
    assert said[0] == "Continuing from 94%, 600 blocks left"
    assert meter.cached == 9_400


def test_progress_never_moves_backwards_and_gives_an_eta():
    said: list[str] = []
    stats: dict[str, int] = {}
    clock = _Clock()
    meter = BlockMeter(1_000, ("swaps", "transfers"), said.append, stats, clock)
    meter.begin()
    clock.now = 10
    meter.advance("swaps", 400, found=7)
    shown = len(said)
    meter.advance("transfers", 0)
    assert len(said) == shown
    meter.advance("transfers", 200)
    assert said[-1].endswith("blocks 300 of 1,000")
    assert stats["swaps"] == 7
    assert stats["eta"] == 23


def test_parallel_workers_cover_every_block_once_even_when_one_times_out():
    seen: list[tuple[int, int]] = []
    lock = threading.Lock()
    slow = {"left": 3}

    def good(frm: int, to: int) -> list[dict]:
        with lock:
            seen.append((frm, to))
        return []

    def flaky(frm: int, to: int) -> list[dict]:
        with lock:
            if slow["left"]:
                slow["left"] -= 1
                raise RpcTimeout("slow")
        return good(frm, to)

    LogScan(1, 10_000, init_span=500).run([good, flaky, good])
    covered = sorted(block for frm, to in seen for block in range(frm, to + 1))
    assert covered == list(range(1, 10_001))


def test_when_one_side_fails_the_other_stops_at_its_next_chunk():
    stopped = threading.Event()

    def fails(_halt) -> None:
        raise ValueError("server gone")

    def waits(halt) -> None:
        for _ in range(500):
            if halt():
                stopped.set()
                return
            threading.Event().wait(0.01)

    with pytest.raises(ValueError):
        _together(lambda: False, fails, waits)
    assert stopped.is_set()


def test_a_running_load_reports_how_long_it_has_been_quiet(monkeypatch):
    release = threading.Event()

    def stuck(*_args, progress, cancel, **_kwargs):
        progress("Reading swaps")
        release.wait(5)
        raise RuntimeError("stopped by the test")

    monkeypatch.setattr("washdetect.api.load_token", stuck)
    monkeypatch.setattr("washdetect.api.get_store", lambda: None)
    client = TestClient(app)
    job_id = client.post("/api/token/load", json={"token": "0x" + "ab" * 20, "days": 1}).json()[
        "job_id"
    ]
    body = client.get(f"/api/jobs/{job_id}").json()
    release.set()
    assert body["status"] == "running"
    assert body["quiet_for"] == 0
    assert "moved_at" not in body


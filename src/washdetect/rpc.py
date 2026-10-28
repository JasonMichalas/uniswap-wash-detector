from __future__ import annotations

import random
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

from washdetect.config import DEFAULT_RPCS

LogFetcher = Callable[[int, int], list[dict[str, Any]]]


class RpcError(Exception):
    pass


class RpcRangeError(RpcError):
    """Split the span. The same range is not retried."""


class RpcRateError(RpcError):
    pass


class RpcTimeout(RpcRateError):
    """No answer in time. For eth_getLogs this means the range is too heavy."""


class Cancelled(Exception):
    """The user stopped this load. Chunks already written stay cached."""


_RANGE_HINTS = (
    "block range",
    "more than",
    "too many",
    "query timeout",
    "timeout",
    "exceed",
    "range is too",
    "10000",
    "10,000",
    "response size",
    "log response size",
    "try with this block range",
    "invalid block range",
    "header not found",
    "history has been pruned",
    "archive request",
    "personal token",
)


_SUGGESTED_RANGE = re.compile(
    r"try with this block range\s*\[\s*(0x[0-9a-f]+|\d+)\s*,\s*(0x[0-9a-f]+|\d+)\s*\]",
    re.IGNORECASE,
)


def _parse_block(text: str) -> int:
    return int(text, 16) if text.lower().startswith("0x") else int(text)


def suggested_span(message: str) -> int | None:
    """Width an endpoint offered after -32005, or nothing if the hint is missing."""
    found = _SUGGESTED_RANGE.search(message)
    if not found:
        return None
    start = _parse_block(found.group(1))
    end = _parse_block(found.group(2))
    if end < start:
        return None
    return max(1, end - start + 1)


def _drpc_cap(url: str) -> int | None:
    # The free plan refuses eth_getLogs wider than 10,000 blocks.
    if "drpc.org" in url.lower():
        return 10_001
    return None


def _keeps_history(url: str, end_block: int | None, head: int | None) -> bool:
    """publicnode answers recent blocks and refuses archive history."""
    if "publicnode" not in url.lower():
        return True
    if head is None or end_block is None:
        return False
    return end_block >= head - 128


def _trusts_empty(url: str) -> bool:
    """Flashbots answers eth_getLogs with [] for ranges it does not store. These do not."""
    host = url.lower()
    return "mevblocker" in host or "drpc.org" in host or "tenderly" in host


def _hard_cap(text: str) -> bool:
    """This endpoint will not serve a range this wide. Another endpoint might."""
    lowered = text.lower()
    return any(hint in lowered for hint in ("free plan", "10000", "10,000", "10 block", "up to a"))


def _logs_width(params: list[Any]) -> int | None:
    if not params or not isinstance(params[0], dict):
        return None
    filt = params[0]
    try:
        start = int(str(filt["fromBlock"]), 16)
        end = int(str(filt["toBlock"]), 16)
    except (KeyError, TypeError, ValueError):
        return None
    return end - start + 1


class EndpointBook:
    """Shared by parallel clients: refused widths, pacing, and how each endpoint has been behaving."""

    cooldown = 30.0
    strikes = 3

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reject: dict[str, int] = {}
        self._next: dict[str, float] = {}
        self._streak: dict[str, int] = {}
        self._cool_until: dict[str, float] = {}
        self._latency: dict[str, float] = {}
        self._no_batch: set[str] = set()

    def allows(self, url: str, width: int | None) -> bool:
        if width is None:
            return True
        with self.lock:
            cap = self.reject.get(url)
        static = _drpc_cap(url)
        if static is not None:
            cap = static if cap is None else min(cap, static)
        return cap is None or width < cap

    def mark_reject(self, url: str, width: int) -> None:
        with self.lock:
            prev = self.reject.get(url)
            self.reject[url] = width if prev is None else min(prev, width)

    def wait_turn(self, url: str, gap: float = 0.08) -> None:
        with self.lock:
            now = time.monotonic()
            due = self._next.get(url, 0.0)
            self._next[url] = max(now, due) + gap
            delay = due - now
        if delay > 0:
            time.sleep(delay)

    def record(self, url: str, ok: bool, seconds: float = 0.0) -> None:
        with self.lock:
            if ok:
                self._streak[url] = 0
                prev = self._latency.get(url, seconds)
                self._latency[url] = 0.7 * prev + 0.3 * seconds
                return
            streak = self._streak.get(url, 0) + 1
            self._streak[url] = streak
            if streak >= self.strikes:
                self._cool_until[url] = time.monotonic() + self.cooldown

    def batches(self, url: str) -> bool:
        with self.lock:
            return url not in self._no_batch

    def refuse_batches(self, url: str) -> None:
        with self.lock:
            self._no_batch.add(url)

    def usable(self, urls: list[str]) -> list[str]:
        """Endpoints that are not resting after repeated failures, quickest first. All of them if every one is."""
        now = time.monotonic()
        with self.lock:
            awake = [url for url in urls if self._cool_until.get(url, 0.0) <= now]
            return sorted(awake or urls, key=lambda url: self._streak.get(url, 0))

    def fastest(self, urls: list[str]) -> list[str]:
        return sorted(self.usable(urls), key=lambda url: self._latency.get(url, 1.0))


class LogScan:
    """Block ranges shared by a pool of workers. A refused, slow, or full range is split and queued again."""

    def __init__(
        self,
        start: int,
        end: int,
        *,
        init_span: int = 5_000,
        min_span: int = 1,
        max_logs: int = 10_000,
        empty_limit: int = 50_000,
        on_note: Callable[[str], None] | None = None,
        on_chunk: Callable[[int, int, list[dict[str, Any]]], None] | None = None,
        on_progress: Callable[[int, int, int], None] | None = None,
        cancel: Callable[[], bool] | None = None,
    ) -> None:
        if init_span < 1 or min_span < 1:
            raise ValueError("span must be at least 1 block")
        self.start = start
        self.end = end
        self.init_span = max(min_span, init_span)
        self.min_span = min_span
        self.max_logs = max_logs
        self.empty_limit = empty_limit
        self.on_note = on_note
        self.on_chunk = on_chunk
        self.on_progress = on_progress
        self.cancel = cancel
        self.span = self.init_span
        self.logs: list[dict[str, Any]] = []
        self._next = start
        self._retry: list[tuple[int, int, int]] = []
        self._busy = 0
        self._done = 0
        self._steps = 0
        self._guard = max(100, (end - start + 1) * 8)
        self._error: BaseException | None = None
        self._cond = threading.Condition()

    def run(self, fetchers: list[LogFetcher]) -> list[dict[str, Any]]:
        if self.start > self.end:
            return []
        if len(fetchers) == 1:
            self._work(fetchers[0])
        else:
            threads = [
                threading.Thread(target=self._work, args=(fetch,), daemon=True)
                for fetch in fetchers
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        if self._error is not None:
            raise self._error
        self.logs.sort(key=_log_sort)
        return self.logs

    def _work(self, fetch: LogFetcher) -> None:
        while (item := self._take()) is not None:
            try:
                self._step(fetch, *item)
            except BaseException as exc:
                with self._cond:
                    self._error = self._error or exc
            finally:
                with self._cond:
                    self._busy -= 1
                    self._cond.notify_all()

    def _take(self) -> tuple[int, int, int] | None:
        with self._cond:
            while True:
                if self._error is not None:
                    return None
                if self.cancel and self.cancel():
                    self._error = Cancelled("Load cancelled")
                    self._cond.notify_all()
                    return None
                self._steps += 1
                if self._steps > self._guard:
                    self._error = RpcError(
                        f"eth_getLogs did not finish between {self.start} and {self.end}; "
                        "the endpoint kept refusing every chunk."
                    )
                    return None
                if self._retry:
                    item = self._retry.pop()
                elif self._next <= self.end:
                    to = min(self._next + self.span - 1, self.end)
                    item = (self._next, to, 0)
                    self._next = to + 1
                elif self._busy == 0:
                    return None
                else:
                    self._steps -= 1
                    self._cond.wait(0.5)
                    continue
                self._busy += 1
                return item

    def _note(self, message: str) -> None:
        if self.on_note:
            self.on_note(message)

    def _split(self, frm: int, to: int, part: int, why: str) -> None:
        part = max(self.min_span, min(part, to - frm))
        with self._cond:
            self.span = min(self.span, part)
            self._retry.append((frm + part, to, 0))
            self._retry.append((frm, frm + part - 1, 0))
        self._note(why)

    def _step(self, fetch: LogFetcher, frm: int, to: int, trouble: int) -> None:
        width = to - frm + 1
        try:
            batch = fetch(frm, to)
        except RpcRangeError as exc:
            if width <= self.min_span:
                raise RpcError(
                    f"eth_getLogs refused even {width} block(s) at {frm}: {exc}"
                ) from exc
            hint = suggested_span(str(exc))
            part = hint if hint is not None and hint < width else width // 2
            self._split(frm, to, part, f"narrowed eth_getLogs chunk to {part} blocks ({exc})")
            return
        except RpcError as exc:
            # A timeout says the range is heavy, so halve it straight away rather than asking again.
            if not isinstance(exc, RpcTimeout) and trouble < 2:
                with self._cond:
                    self._retry.append((frm, to, trouble + 1))
                self._note(f"eth_getLogs failed at block {frm}; retrying ({exc})")
                return
            if width <= self.min_span:
                raise
            self._split(
                frm,
                to,
                width // 2,
                f"narrowed eth_getLogs chunk to {width // 2} blocks after errors ({exc})",
            )
            return

        if not batch and width > self.empty_limit:
            part = min(width // 2, self.empty_limit)
            self._split(
                frm,
                to,
                part,
                f"empty eth_getLogs page of {width} blocks; confirming in chunks of {part}",
            )
            return
        if len(batch) >= self.max_logs and width > self.min_span:
            self._split(
                frm,
                to,
                width // 2,
                f"log page returned {len(batch)} rows (the cap); retrying from block {frm} in smaller chunks",
            )
            return
        if len(batch) >= self.max_logs:
            self._note(
                f"block {frm}-{to} returned {len(batch)} logs at the minimum chunk size; "
                "keeping them, but the page may be truncated"
            )
        if self.on_chunk:
            self.on_chunk(frm, to, batch)
        with self._cond:
            self.logs.extend(batch)
            self._done += width
            if len(batch) < self.max_logs // 4 and self.span < self.init_span:
                self.span = min(self.init_span, max(self.span * 2, self.min_span))
            done, found = self._done, len(self.logs)
        if self.on_progress:
            self.on_progress(self.start + done - 1, self.end, found)


def fetch_logs_adaptive(
    start: int,
    end: int,
    fetch: LogFetcher,
    **options: Any,
) -> list[dict[str, Any]]:
    """A full page may be a silent truncation, so a chunk at the cap is retried smaller."""
    return LogScan(start, end, **options).run([fetch])


def _error_text(body: Any, status: int) -> str:
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return f"{status} {err.get('code', '')} {err.get('message', '')}".lower()
        if err:
            return f"{status} {err}".lower()
    return f"{status} {body!s}".lower()[:500]


def classify_error(status: int, body: Any) -> RpcError:
    text = _error_text(body, status)
    if status in {408, 429, 502, 503, 504} or "rate" in text or "too many request" in text:
        return RpcRateError(text)
    if any(hint in text for hint in _RANGE_HINTS):
        return RpcRangeError(text)
    # A bare -32000 is not "range too wide"; only the hint strings above are.
    if status >= 500:
        return RpcRateError(text)
    return RpcError(text)


class RpcClient:
    def __init__(
        self,
        endpoints: list[str] | tuple[str, ...] | None = None,
        *,
        transport: Callable[[str, Any], Any] | None = None,
        sleep: Callable[[float], None] | None = None,
        timeout: float = 12.0,
        max_attempts: int = 8,
    ) -> None:
        self.endpoints = list(endpoints or DEFAULT_RPCS)
        if not self.endpoints:
            raise ValueError("at least one RPC endpoint is required")
        self._cursor = 0
        self._transport = transport
        self._sleep = sleep or time.sleep
        self._timeout = timeout
        self._max_attempts = max_attempts
        self.notes: list[str] = []
        self.details: list[str] = []
        self._head: int | None = None
        self._id = 1
        self._http: httpx.Client | None = None
        self._lock = threading.Lock()
        self.book = EndpointBook()

    def note(self, message: str) -> None:
        with self._lock:
            self.notes.append(message)

    def detail(self, message: str) -> None:
        """A refusal the loader recovered from. Kept for the details disclosure, not the banner."""
        with self._lock:
            if message in self.details or len(self.details) >= 8:
                return
            self.details.append(message)

    def spawn(self, cursor: int | None = None) -> RpcClient:
        """A client for another thread. It shares notes, endpoint health, and request ids with this one."""
        child = RpcClient(
            self.endpoints,
            transport=self._transport,
            sleep=self._sleep,
            timeout=self._timeout,
            max_attempts=self._max_attempts,
        )
        child.book = self.book
        child.notes = self.notes
        child.details = self.details
        child._lock = self._lock
        child._head = self._head
        child._cursor = self._cursor if cursor is None else cursor
        return child

    def log_workers(self, width: int | None = None) -> int:
        """Two requests in flight per endpoint that keeps history; more starts tripping rate limits."""
        if self._transport is not None:
            return 1
        able = [url for url in self.archive_endpoints() if self.book.allows(url, width)]
        return max(1, min(6, 2 * len(able)))

    def archive_endpoints(self) -> list[str]:
        return [url for url in self.endpoints if _trusts_empty(url)]

    def _next_id(self) -> int:
        with self._lock:
            value = self._id
            self._id += 1
            return value

    def _endpoint(self) -> str:
        return self.endpoints[self._cursor % len(self.endpoints)]

    def _rotate(self) -> None:
        self._cursor += 1

    def _client(self) -> httpx.Client:
        with self._lock:
            if self._http is None:
                self._http = httpx.Client(timeout=httpx.Timeout(self._timeout, connect=5.0))
            return self._http

    def _post(self, url: str, payload: Any) -> Any:
        if self._transport is not None:
            return self._transport(url, payload)
        started = time.monotonic()
        try:
            response = self._client().post(url, json=payload)
        except httpx.TimeoutException as exc:
            self.book.record(url, False)
            raise RpcTimeout(f"no answer from {url} within {self._timeout:.0f}s") from exc
        except httpx.HTTPError:
            self.book.record(url, False)
            raise
        try:
            body = response.json()
        except Exception as exc:
            self.book.record(url, False)
            raise RpcRateError(f"{response.status_code} non-json body") from exc
        if response.status_code >= 400:
            err = classify_error(response.status_code, body)
            self.book.record(url, not isinstance(err, RpcRateError))
            raise err
        self.book.record(url, True, time.monotonic() - started)
        return body

    def _pause(self, attempt: int) -> None:
        # Another endpoint is tried at once; only a second lap round all of them waits.
        if attempt >= len(self.endpoints):
            self._sleep(self._backoff(attempt - len(self.endpoints)))

    def call(self, method: str, params: list[Any]) -> Any:
        if method == "eth_getLogs":
            return self._call_logs(params)
        last: Exception | None = None
        for attempt in range(self._max_attempts):
            url = self._endpoint()
            payload = {"jsonrpc": "2.0", "id": self._next_id(), "method": method, "params": params}
            try:
                body = self._post(url, payload)
                if isinstance(body, dict) and body.get("error"):
                    raise classify_error(200, body)
                if not isinstance(body, dict) or "result" not in body:
                    raise RpcError(f"missing result from {url}")
                return body["result"]
            except (RpcError, httpx.HTTPError) as exc:
                # Outside eth_getLogs a "range" refusal means this node lacks the history.
                last = exc
                self.detail(f"{method} failed on {url}: {exc}")
                self._rotate()
                self._pause(attempt)
        raise RpcError(f"{method} failed after {self._max_attempts} attempts: {last}")

    def batch(
        self, calls: list[tuple[str, list[Any]]], *, batch_size: int = 100, workers: int = 6
    ) -> list[Any]:
        """Chunks go to different endpoints at once; a chunk that fails moves on, then falls back to single calls."""
        chunks = [
            calls[offset : offset + batch_size] for offset in range(0, len(calls), batch_size)
        ]
        if self._transport is not None or len(chunks) <= 1:
            parts = [self._batch_chunk(index, chunk) for index, chunk in enumerate(chunks)]
        else:
            with ThreadPoolExecutor(max_workers=min(workers, len(chunks))) as pool:
                parts = list(pool.map(self._batch_chunk, range(len(chunks)), chunks))
        return [item for part in parts for item in part]

    def _batch_chunk(self, index: int, chunk: list[tuple[str, list[Any]]]) -> list[Any]:
        ranked = [url for url in self.book.fastest(self.endpoints) if self.book.batches(url)]
        start = index % max(1, len(ranked))
        results: list[Any] = [None] * len(chunk)
        for url in (ranked[start:] + ranked[:start])[:4]:
            ids = [self._next_id() for _ in chunk]
            payload = [
                {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params}
                for rpc_id, (method, params) in zip(ids, chunk, strict=True)
            ]
            try:
                body = self._post(url, payload)
                if not isinstance(body, list):
                    raise (
                        classify_error(200, body)
                        if isinstance(body, dict)
                        else RpcError("not a list")
                    )
            except (RpcError, httpx.HTTPError) as exc:
                self.book.refuse_batches(url)
                self.detail(f"batch of {len(chunk)} failed on {url}: {exc}")
                continue
            by_id = {item.get("id"): item for item in body if isinstance(item, dict)}
            results = [(by_id.get(rpc_id) or {}).get("result") for rpc_id in ids]
            # Some endpoints cap a batch or keep no history and answer with errors; ask the next one.
            if sum(item is None for item in results) * 2 <= len(results):
                break
            self.detail(f"batch of {len(chunk)} on {url} came back mostly empty")
        return [
            item if item is not None else self._single(method, params)
            for item, (method, params) in zip(results, chunk, strict=True)
        ]

    def _single(self, method: str, params: list[Any]) -> Any:
        try:
            return self.call(method, params)
        except RpcError:
            return None

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(8.0, 0.4 * (2**attempt)) + random.random() * 0.2

    def _pick_log_endpoint(
        self, width: int | None, tried: set[str], end_block: int | None
    ) -> str | None:
        archive = [url for url in self.endpoints if _trusts_empty(url)]
        others = [url for url in self.endpoints if not _trusts_empty(url)]
        ordered = archive + others
        # Workers get different cursors so parallel chunks do not all start on the first endpoint.
        start = self._cursor % len(ordered)
        rotated = ordered[start:] + ordered[:start]
        awake = set(self.book.usable(rotated))
        for url in sorted(rotated, key=lambda item: item not in awake):
            if url in tried or not self.book.allows(url, width):
                continue
            if not _keeps_history(url, end_block, self._head):
                continue
            return url
        return None

    def _call_logs(self, params: list[Any]) -> Any:
        """Try the next endpoint when one refuses a wide eth_getLogs, then shrink only if all refuse."""
        width = _logs_width(params)
        end_block = None
        if params and isinstance(params[0], dict) and "toBlock" in params[0]:
            try:
                end_block = int(str(params[0]["toBlock"]), 16)
            except (TypeError, ValueError):
                end_block = None
        last_range: Exception | None = None
        last_other: Exception | None = None
        timeouts = 0
        untrusted_empty: list[Any] | None = None
        tried: set[str] = set()
        archive_tries: dict[str, int] = {}
        attempts = 0
        while attempts < self._max_attempts:
            url = self._pick_log_endpoint(width, tried, end_block)
            if url is None:
                break
            # One extra try on an archive endpoint, so a single 503 does not shrink the chunk.
            if _trusts_empty(url) and archive_tries.get(url, 0) < 1:
                archive_tries[url] = 1
            else:
                tried.add(url)
            attempts += 1
            if self._transport is None:
                self.book.wait_turn(url)
            payload = {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "eth_getLogs",
                "params": params,
            }
            try:
                body = self._post(url, payload)
                if isinstance(body, dict) and body.get("error"):
                    raise classify_error(200, body)
                if not isinstance(body, dict) or "result" not in body:
                    raise RpcError(f"missing result from {url}")
            except RpcRangeError as exc:
                last_range = exc
                if width is not None and _hard_cap(str(exc)):
                    self.book.mark_reject(url, width)
                self.detail(f"eth_getLogs refused on {url}: {exc}")
                continue
            except RpcTimeout as exc:
                # A slow answer is about the range, not the endpoint; do not ask it the same thing again.
                timeouts += 1
                last_other = exc
                tried.add(url)
                self.detail(f"eth_getLogs timed out on {url}")
                continue
            except (RpcError, httpx.HTTPError) as exc:
                last_other = exc
                self.detail(f"eth_getLogs failed on {url}: {exc}")
                self._pause(attempts)
                continue
            result = body["result"]
            if isinstance(result, list) and len(result) == 0 and not _trusts_empty(url):
                untrusted_empty = result
                self.detail(f"eth_getLogs on {url} returned no logs; asking an archive endpoint")
                continue
            return result
        # An empty page is only an answer from an endpoint that stores the history.
        # Flashbots returns [] for ranges it does not keep; saving that would hide pools.
        if last_range is not None and (last_other is None or timeouts):
            raise last_range
        if timeouts:
            raise RpcTimeout(f"eth_getLogs timed out on {timeouts} endpoint(s)")
        if last_other is not None:
            if isinstance(last_other, RpcError):
                raise last_other
            raise RpcError(str(last_other)) from last_other
        if untrusted_empty is not None:
            raise RpcError(
                "eth_getLogs returned no logs from an endpoint that does not keep this history"
            )
        raise RpcError(f"eth_getLogs failed after {attempts} attempts")

    def block_number(self) -> int:
        number = int(self.call("eth_blockNumber", []), 16)
        self._head = number
        return number

    def get_block(self, number: int) -> dict[str, Any]:
        block = self.call("eth_getBlockByNumber", [hex(number), False])
        if not isinstance(block, dict):
            raise RpcError(f"block {number} was not found")
        return block

    def eth_call(self, to: str, data: str, block: int | str = "latest") -> str:
        tag = hex(block) if isinstance(block, int) else block
        result = self.call("eth_call", [{"to": to, "data": data}, tag])
        if not isinstance(result, str):
            raise RpcError(f"eth_call to {to} returned {result!r}")
        return result

    def log_fetcher(self, address: str | list[str], topics: list[Any]) -> LogFetcher:
        def fetch(frm: int, to: int) -> list[dict[str, Any]]:
            params = {
                "fromBlock": hex(frm),
                "toBlock": hex(to),
                "address": address,
                "topics": topics,
            }
            result = self.call("eth_getLogs", [params])
            if not isinstance(result, list):
                raise RpcError(f"eth_getLogs returned {type(result).__name__}")
            return result

        return fetch

    def get_logs(
        self,
        *,
        address: str | list[str],
        topics: list[Any],
        start_block: int,
        end_block: int,
        init_span: int = 4_000,
        workers: int = 1,
        on_chunk: Callable[[int, int, list[dict[str, Any]]], None] | None = None,
        on_progress: Callable[[int, int, int], None] | None = None,
        cancel: Callable[[], bool] | None = None,
        empty_limit: int = 50_000,
    ) -> list[dict[str, Any]]:
        if start_block > end_block:
            return []
        chunks = -(-(end_block - start_block + 1) // init_span)
        count = 1 if self._transport is not None else max(1, min(workers, chunks))
        clients = [self] + [self.spawn(cursor=self._cursor + index) for index in range(1, count)]
        scan = LogScan(
            start_block,
            end_block,
            init_span=init_span,
            on_note=self.detail,
            on_chunk=on_chunk,
            on_progress=on_progress,
            cancel=cancel,
            empty_limit=empty_limit,
        )
        try:
            return scan.run([client.log_fetcher(address, topics) for client in clients])
        finally:
            for child in clients[1:]:
                child.close()

    def find_block_by_timestamp(self, target_ts: int, lo: int, hi: int) -> int:
        best = lo
        while lo <= hi:
            mid = (lo + hi) // 2
            block = self.get_block(mid)
            ts = int(block["timestamp"], 16)
            if ts <= target_ts:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        return best

    def close(self) -> None:
        with self._lock:
            http, self._http = self._http, None
        if http is not None:
            http.close()


def _log_sort(log: dict[str, Any]) -> tuple[int, int]:
    try:
        block = int(str(log.get("blockNumber", "0x0")), 16)
    except (TypeError, ValueError):
        block = 0
    try:
        index = int(str(log.get("logIndex", "0x0")), 16)
    except (TypeError, ValueError):
        index = 0
    return block, index

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
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


def _trusts_empty(url: str) -> bool:
    """Flashbots answers eth_getLogs with [] for ranges it does not store. These two do not."""
    host = url.lower()
    return "mevblocker" in host or "drpc.org" in host


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


class SpanBook:
    """Per-endpoint widest range that was refused, shared by parallel clients."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reject: dict[str, int] = {}
        self._next: dict[str, float] = {}

    def allows(self, url: str, width: int | None) -> bool:
        if width is None:
            return True
        with self.lock:
            cap = self.reject.get(url)
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


def fetch_logs_adaptive(
    start: int,
    end: int,
    fetch: LogFetcher,
    *,
    init_span: int = 5_000,
    min_span: int = 1,
    max_logs: int = 8_000,
    on_note: Callable[[str], None] | None = None,
    on_chunk: Callable[[int, int, list[dict[str, Any]]], None] | None = None,
    on_progress: Callable[[int, int, int], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> list[dict[str, Any]]:
    """A full page may be a silent truncation, so a chunk at the cap is retried smaller."""
    if start > end:
        return []
    if init_span < 1 or min_span < 1:
        raise ValueError("span must be at least 1 block")

    logs: list[dict[str, Any]] = []
    span = max(min_span, init_span)
    block = start
    steps = 0
    trouble = 0
    guard = max(100, (end - start + 1) * 8)
    # A wide empty page is not saved. One pruned endpoint answers [] for history it does not store.
    empty_limit = 50_000

    while block <= end:
        steps += 1
        if cancel and cancel():
            raise Cancelled("Load cancelled")
        if steps > guard:
            raise RpcError(
                f"eth_getLogs did not finish between {start} and {end}; "
                "the endpoint kept refusing every chunk."
            )
        to = min(block + span - 1, end)
        try:
            batch = fetch(block, to)
        except RpcRangeError as exc:
            width = to - block + 1
            if width <= min_span:
                raise RpcError(
                    f"eth_getLogs refused even {width} block(s) at {block}: {exc}"
                ) from exc
            span = max(min_span, span // 2)
            trouble = 0
            if on_note:
                on_note(f"narrowed eth_getLogs chunk to {span} blocks ({exc})")
            continue
        except RpcError as exc:
            trouble += 1
            if trouble <= 2:
                if on_note:
                    on_note(f"eth_getLogs failed at block {block}; retrying ({exc})")
                continue
            width = to - block + 1
            if width <= min_span:
                raise
            span = max(min_span, span // 2)
            trouble = 0
            if on_note:
                on_note(f"narrowed eth_getLogs chunk to {span} blocks after errors ({exc})")
            continue

        width = to - block + 1
        if len(batch) == 0 and width > empty_limit:
            span = max(min_span, min(span // 2, empty_limit))
            if on_note:
                on_note(f"empty eth_getLogs page of {width} blocks; confirming in chunks of {span}")
            continue

        if len(batch) >= max_logs and width > min_span:
            span = max(min_span, width // 2)
            if on_note:
                on_note(
                    f"log page returned {len(batch)} rows (the cap); "
                    f"retrying from block {block} in chunks of {span}"
                )
            continue
        if len(batch) >= max_logs and on_note:
            on_note(
                f"block {block}-{to} returned {len(batch)} logs at the minimum "
                "chunk size; keeping them, but the page may be truncated"
            )
        logs.extend(batch)
        if on_chunk:
            on_chunk(block, to, batch)
        if on_progress:
            on_progress(to, end, len(logs))
        block = to + 1
        if len(batch) < max_logs // 4 and span < init_span:
            span = min(init_span, max(span * 2, min_span))
    return logs


class RpcClient:

    def __init__(
        self,
        endpoints: list[str] | tuple[str, ...] | None = None,
        *,
        transport: Callable[[str, Any], Any] | None = None,
        sleep: Callable[[float], None] | None = None,
        timeout: float = 40.0,
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
        self._id = 1
        self._http: httpx.Client | None = None
        self._lock = threading.Lock()
        self.spans = SpanBook()

    def note(self, message: str) -> None:
        with self._lock:
            self.notes.append(message)

    def spawn(self) -> RpcClient:
        child = RpcClient(
            self.endpoints,
            transport=self._transport,
            sleep=self._sleep,
            timeout=self._timeout,
            max_attempts=self._max_attempts,
        )
        child.spans = self.spans
        return child

    def _next_id(self) -> int:
        with self._lock:
            value = self._id
            self._id += 1
            return value

    def _endpoint(self) -> str:
        return self.endpoints[self._cursor % len(self.endpoints)]

    def _rotate(self) -> None:
        self._cursor += 1

    def _post(self, url: str, payload: Any) -> Any:
        if self._transport is not None:
            return self._transport(url, payload)
        if self._http is None:
            self._http = httpx.Client(timeout=self._timeout)
        response = self._http.post(url, json=payload)
        try:
            body = response.json()
        except Exception as exc:
            raise RpcRateError(f"{response.status_code} non-json body") from exc
        if response.status_code >= 400:
            raise classify_error(response.status_code, body)
        return body

    def call(self, method: str, params: list[Any]) -> Any:
        if method == "eth_getLogs":
            return self._call_logs(params)
        last: Exception | None = None
        for attempt in range(self._max_attempts):
            url = self._endpoint()
            payload = {"jsonrpc": "2.0", "id": self._next_id(), "method": method, "params": params}
            try:
                body = self._post(url, payload)
            except RpcRangeError:
                raise
            except (RpcRateError, RpcError, httpx.HTTPError) as exc:
                last = exc
                self.note(f"{method} failed on {url}: {exc}")
                self._rotate()
                self._sleep(self._backoff(attempt))
                continue
            if isinstance(body, dict) and body.get("error"):
                err = classify_error(200, body)
                if isinstance(err, RpcRangeError):
                    raise err
                last = err
                self.note(f"{method} error on {url}: {err}")
                self._rotate()
                self._sleep(self._backoff(attempt))
                continue
            if not isinstance(body, dict) or "result" not in body:
                last = RpcError(f"missing result from {url}")
                self._rotate()
                continue
            return body["result"]
        raise RpcError(f"{method} failed after {self._max_attempts} attempts: {last}")

    def batch(self, calls: list[tuple[str, list[Any]]], *, batch_size: int = 20) -> list[Any]:
        results: list[Any] = [None] * len(calls)
        for offset in range(0, len(calls), batch_size):
            chunk = calls[offset : offset + batch_size]
            payload = []
            ids = []
            for method, params in chunk:
                rpc_id = self._next_id()
                payload.append(
                    {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params}
                )
                ids.append(rpc_id)
            try:
                body = self._post(self._endpoint(), payload)
                if isinstance(body, dict) and body.get("error"):
                    raise classify_error(200, body)
                if not isinstance(body, list):
                    raise RpcError("batch response was not a list")
                by_id = {item.get("id"): item for item in body if isinstance(item, dict)}
                for local, rpc_id in enumerate(ids):
                    item = by_id.get(rpc_id) or {}
                    if item.get("error"):
                        raise classify_error(200, item)
                    results[offset + local] = item.get("result")
            except (RpcError, httpx.HTTPError):
                self._rotate()
                for local, (method, params) in enumerate(chunk):
                    results[offset + local] = self.call(method, params)
        return results

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(8.0, 0.4 * (2**attempt)) + random.random() * 0.2

    def _pick_log_endpoint(self, width: int | None, tried: set[str]) -> str | None:
        archive = [url for url in self.endpoints if _trusts_empty(url)]
        others = [url for url in self.endpoints if not _trusts_empty(url)]
        ordered = archive + others
        if not ordered:
            return None
        # Workers set _cursor to their index so parallel chunks do not all start
        # on the first endpoint. Cursor 0 keeps the original order.
        start = self._cursor % len(ordered)
        rotated = ordered[start:] + ordered[:start]
        for url in rotated:
            if url in tried or not self.spans.allows(url, width):
                continue
            return url
        return None

    def _call_logs(self, params: list[Any]) -> Any:
        """Try the next endpoint when one refuses a wide eth_getLogs, then shrink only if all refuse."""
        width = _logs_width(params)
        last_range: Exception | None = None
        last_other: Exception | None = None
        untrusted_empty: list[Any] | None = None
        tried: set[str] = set()
        archive_tries: dict[str, int] = {}
        attempts = 0
        while attempts < self._max_attempts:
            url = self._pick_log_endpoint(width, tried)
            if url is None:
                break
            # One extra try on the archive endpoint. A single 503 should not shrink the chunk.
            if _trusts_empty(url) and archive_tries.get(url, 0) < 1:
                archive_tries[url] = 1
            else:
                tried.add(url)
            attempts += 1
            if self._transport is None:
                self.spans.wait_turn(url)
            payload = {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "eth_getLogs",
                "params": params,
            }
            try:
                body = self._post(url, payload)
            except RpcRangeError as exc:
                last_range = exc
                if width is not None and _hard_cap(str(exc)):
                    self.spans.mark_reject(url, width)
                self.note(f"eth_getLogs refused on {url}: {exc}")
                continue
            except (RpcRateError, RpcError, httpx.HTTPError) as exc:
                last_other = exc
                self.note(f"eth_getLogs failed on {url}: {exc}")
                self._sleep(self._backoff(attempts))
                continue
            if isinstance(body, dict) and body.get("error"):
                err = classify_error(200, body)
                if isinstance(err, RpcRangeError):
                    last_range = err
                    if width is not None and _hard_cap(str(err)):
                        self.spans.mark_reject(url, width)
                    self.note(f"eth_getLogs refused on {url}: {err}")
                    continue
                last_other = err
                self.note(f"eth_getLogs error on {url}: {err}")
                self._sleep(self._backoff(attempts))
                continue
            if not isinstance(body, dict) or "result" not in body:
                last_other = RpcError(f"missing result from {url}")
                continue
            result = body["result"]
            if isinstance(result, list) and len(result) == 0 and not _trusts_empty(url):
                untrusted_empty = result
                self.note(f"eth_getLogs on {url} returned no logs; asking an archive endpoint")
                continue
            return result
        # An empty page is only an answer from an endpoint that stores the history.
        # Flashbots returns [] for ranges it does not keep; saving that would hide pools.
        if last_range is not None and last_other is None:
            raise last_range
        if last_other is not None:
            if isinstance(last_other, RpcError):
                raise last_other
            raise RpcError(str(last_other)) from last_other
        if untrusted_empty is not None:
            raise RpcError("eth_getLogs returned no logs from an endpoint that does not keep this history")
        raise RpcError(f"eth_getLogs failed after {attempts} attempts: {last_other}")

    def block_number(self) -> int:
        return int(self.call("eth_blockNumber", []), 16)

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
    ) -> list[dict[str, Any]]:
        if start_block > end_block:
            return []
        width = end_block - start_block + 1
        use_workers = 1 if self._transport is not None else max(1, workers)
        if use_workers <= 1 or width < init_span * 2:
            return self._logs_span(
                address, topics, start_block, end_block, init_span, on_chunk, on_progress, cancel
            )
        parts = _split_range(start_block, end_block, min(use_workers, 4, max(1, width // init_span)))
        found: list[dict[str, Any]] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def run(span: tuple[int, int], client: RpcClient) -> None:
            try:
                rows = client._logs_span(
                    address, topics, span[0], span[1], init_span, on_chunk, on_progress, cancel
                )
                with lock:
                    found.extend(rows)
            except BaseException as exc:
                with lock:
                    errors.append(exc)
            finally:
                client.close()

        threads: list[threading.Thread] = []
        children: list[RpcClient] = []
        for index, span in enumerate(parts):
            client = self.spawn()
            client._cursor = self._cursor + index
            children.append(client)
            thread = threading.Thread(target=run, args=(span, client), daemon=True)
            thread.start()
            threads.append(thread)
        for thread in threads:
            thread.join()
        for child in children:
            self.notes.extend(child.notes)
        cancelled = [item for item in errors if isinstance(item, Cancelled)]
        if cancelled:
            raise cancelled[0]
        if errors:
            raise errors[0]
        found.sort(key=_log_sort)
        return found

    def _logs_span(
        self,
        address: str | list[str],
        topics: list[Any],
        start_block: int,
        end_block: int,
        init_span: int,
        on_chunk: Callable[[int, int, list[dict[str, Any]]], None] | None,
        on_progress: Callable[[int, int, int], None] | None,
        cancel: Callable[[], bool] | None,
    ) -> list[dict[str, Any]]:
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

        return fetch_logs_adaptive(
            start_block,
            end_block,
            fetch,
            init_span=init_span,
            on_note=self.note,
            on_chunk=on_chunk,
            on_progress=on_progress,
            cancel=cancel,
        )

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
        if self._http is not None:
            self._http.close()
            self._http = None


def _split_range(start: int, end: int, parts: int) -> list[tuple[int, int]]:
    width = end - start + 1
    step = max(1, (width + parts - 1) // parts)
    spans: list[tuple[int, int]] = []
    block = start
    while block <= end:
        spans.append((block, min(end, block + step - 1)))
        block += step
    return spans


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

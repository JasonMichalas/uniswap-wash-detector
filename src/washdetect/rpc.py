"""Keyless JSON-RPC client with rotation, backoff, and adaptive log ranges.

Public Ethereum endpoints cap `eth_getLogs` by block span and by result
count, and they do it with different error strings. `fetch_logs_adaptive`
is the piece tests cover: it asks for a window, and whenever the fetcher
says the range is too wide it halves the span and retries, without gaps.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from typing import Any

import httpx

from washdetect.config import DEFAULT_RPCS

LogFetcher = Callable[[int, int], list[dict[str, Any]]]


class RpcError(Exception):
    """A call failed after retries, or the response was not usable."""


class RpcRangeError(RpcError):
    """The node refused this block span or this result size.

    The adaptive fetcher treats this as "split the range", not as a fatal
    error. It is deliberately not retried on the same span.
    """


class RpcRateError(RpcError):
    """429 / 503 / 'rate limit'. The client rotates endpoints and backs off."""


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
)


def _error_text(body: Any, status: int) -> str:
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return f"{status} {err.get('code', '')} {err.get('message', '')}".lower()
        if err:
            return f"{status} {err}".lower()
    return f"{status} {body!s}".lower()[:500]


def classify_error(status: int, body: Any) -> RpcError:
    """Turn an HTTP/JSON-RPC failure into a range error or a rate error."""
    text = _error_text(body, status)
    if status in {408, 429, 502, 503, 504} or "rate" in text or "too many request" in text:
        return RpcRateError(text)
    if any(hint in text for hint in _RANGE_HINTS):
        return RpcRangeError(text)
    # Some gateways return a generic -32000 for oversized logs. If the span
    # was large the caller can still decide to split; we only mark the
    # obvious range failures here.
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
) -> list[dict[str, Any]]:
    """Fetch logs over an inclusive block range, shrinking the chunk on refusal.

    `fetch(from_block, to_block)` returns the logs for that inclusive span or
    raises `RpcRangeError`. Successful chunks are concatenated. A chunk that
    comes back at the `max_logs` ceiling is discarded and retried smaller,
    because several public nodes silently truncate. At `min_span` the chunk
    is accepted anyway and a note is recorded: a single very busy block can
    legitimately hold more than `max_logs` events.
    """
    if start > end:
        return []
    if init_span < 1 or min_span < 1:
        raise ValueError("span must be at least 1 block")

    logs: list[dict[str, Any]] = []
    span = max(min_span, init_span)
    block = start
    steps = 0
    # Each block is visited a handful of times while the span shrinks.
    guard = max(100, (end - start + 1) * 8)

    while block <= end:
        steps += 1
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
            if on_note:
                on_note(f"narrowed eth_getLogs chunk to {span} blocks ({exc})")
            continue

        width = to - block + 1
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
        block = to + 1
        if len(batch) < max_logs // 4 and span < init_span:
            span = min(init_span, max(span * 2, min_span))
    return logs


class RpcClient:
    """JSON-RPC over HTTPS. One method, many endpoints.

    `transport` is a test hook: `(url, payload) -> decoded JSON`. Production
    uses httpx. `sleep` is injectable so tests do not actually wait.
    """

    def __init__(
        self,
        endpoints: list[str] | tuple[str, ...] | None = None,
        *,
        transport: Callable[[str, Any], Any] | None = None,
        sleep: Callable[[float], None] | None = None,
        timeout: float = 40.0,
        max_attempts: int = 5,
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

    def note(self, message: str) -> None:
        self.notes.append(message)

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
        """Call one JSON-RPC method. Rotates and backs off on rate limits."""
        last: Exception | None = None
        for attempt in range(self._max_attempts):
            url = self._endpoint()
            payload = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
            self._id += 1
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
        """Run many calls. Results are returned in request order.

        Public gateways often cap batch size, so this slices the work. A
        failed batch falls back to single calls, which already rotate.
        """
        results: list[Any] = [None] * len(calls)
        for offset in range(0, len(calls), batch_size):
            chunk = calls[offset : offset + batch_size]
            payload = []
            ids = []
            for method, params in chunk:
                payload.append(
                    {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
                )
                ids.append(self._id)
                self._id += 1
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
        )

    def find_block_by_timestamp(self, target_ts: int, lo: int, hi: int) -> int:
        """Highest block whose timestamp is <= target_ts. About log2(span) calls."""
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

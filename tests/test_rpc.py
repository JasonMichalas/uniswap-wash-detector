import time

import httpx
import pytest

from washdetect.rpc import RpcClient, RpcError, RpcRangeError, RpcRateError, fetch_logs_adaptive


def test_adaptive_splits_until_the_range_fits():
    calls: list[tuple[int, int]] = []

    def fetch(start: int, end: int) -> list[dict]:
        calls.append((start, end))
        if end - start + 1 > 1000:
            raise RpcRangeError("block range exceeds 1000")
        return [{"blockNumber": hex(block)} for block in range(start, end + 1, 100)]

    logs = fetch_logs_adaptive(0, 2499, fetch, init_span=4000, min_span=1)
    assert all(end - start + 1 <= 1000 for start, end in calls if not (end - start + 1 > 1000))
    successful = [span for span in calls if span[1] - span[0] + 1 <= 1000]
    covered: set[int] = set()
    for start, end in successful:
        covered.update(range(start, end + 1))
    assert covered == set(range(0, 2500))
    assert logs


def test_adaptive_shrinks_when_a_page_hits_the_cap():
    def fetch(start: int, end: int) -> list[dict]:
        width = end - start + 1
        if width > 10:
            return [{"i": i} for i in range(50)]
        return [{"i": i} for i in range(start, end + 1)]

    logs = fetch_logs_adaptive(0, 30, fetch, init_span=40, min_span=1, max_logs=50)
    assert len(logs) == 31
    assert [row["i"] for row in logs] == list(range(31))


def test_client_rotates_after_a_rate_limit(monkeypatch):
    monkeypatch.setattr("washdetect.rpc.random.random", lambda: 0.0)
    seen: list[str] = []

    def transport(url: str, payload: dict) -> dict:
        seen.append(url)
        if len(seen) == 1:
            raise RpcRateError("429 rate limit")
        return {"jsonrpc": "2.0", "id": payload["id"], "result": "0x10"}

    client = RpcClient(
        ["https://a.example", "https://b.example"],
        transport=transport,
        sleep=lambda _seconds: None,
    )
    assert client.call("eth_blockNumber", []) == "0x10"
    assert seen == ["https://a.example", "https://b.example"]


def test_get_logs_skips_an_endpoint_that_rejects_wide_ranges():
    seen: list[tuple[str, int]] = []

    def transport(url: str, payload: dict) -> dict:
        filt = payload["params"][0]
        width = int(filt["toBlock"], 16) - int(filt["fromBlock"], 16) + 1
        seen.append((url, width))
        if url.endswith("narrow.example"):
            return {
                "jsonrpc": "2.0",
                "id": payload["id"],
                "error": {"code": 35, "message": "ranges over 10000 blocks are not supported on free plan"},
            }
        return {"jsonrpc": "2.0", "id": payload["id"], "result": [{"blockNumber": hex(1)}]}

    client = RpcClient(
        ["https://narrow.example", "https://wide.example"],
        transport=transport,
        sleep=lambda _seconds: None,
    )
    logs = client.get_logs(
        address="0x" + "11" * 20,
        topics=[],
        start_block=1,
        end_block=500,
        init_span=500,
    )
    assert len(logs) == 1
    assert seen[0][0].endswith("narrow.example")
    assert any(url.endswith("wide.example") and width == 500 for url, width in seen)


def test_empty_log_page_from_a_pruned_endpoint_is_not_the_answer():
    def transport(url: str, payload: dict) -> dict:
        if "mevblocker" in url or "drpc.org" in url:
            raise RpcRateError("429 rate limit")
        if "flashbots" in url:
            return {"jsonrpc": "2.0", "id": payload["id"], "result": []}
        return {"jsonrpc": "2.0", "id": payload["id"], "result": [{"blockNumber": "0x10"}]}

    client = RpcClient(
        [
            "https://rpc.mevblocker.io",
            "https://eth.drpc.org",
            "https://rpc.flashbots.net",
            "https://ethereum-rpc.publicnode.com",
        ],
        transport=transport,
        sleep=lambda _seconds: None,
        max_attempts=6,
    )
    logs = client.get_logs(
        address="0x" + "22" * 20,
        topics=[],
        start_block=1,
        end_block=100,
        init_span=100,
    )
    assert logs == [{"blockNumber": "0x10"}]


def test_an_empty_page_from_a_pruned_endpoint_is_not_saved_when_archive_is_down():
    def transport(url: str, payload: dict) -> dict:
        if "flashbots" in url:
            return {"jsonrpc": "2.0", "id": payload["id"], "result": []}
        raise RpcRateError("503 unavailable")

    client = RpcClient(
        [
            "https://rpc.mevblocker.io",
            "https://eth.drpc.org",
            "https://rpc.flashbots.net",
        ],
        transport=transport,
        sleep=lambda _seconds: None,
        max_attempts=8,
    )
    with pytest.raises(RpcError):
        client.get_logs(
            address="0x" + "33" * 20,
            topics=[],
            start_block=1,
            end_block=100,
            init_span=100,
        )


def test_log_chunks_rotate_across_endpoints():
    client = RpcClient(
        ["https://one.example", "https://two.example"],
        transport=lambda _url, payload: payload,
        sleep=lambda _seconds: None,
    )
    assert client._pick_log_endpoint(100, set()).endswith("one.example")
    client._cursor = 1
    assert client._pick_log_endpoint(100, set()).endswith("two.example")


def test_adaptive_regrows_the_chunk_after_a_small_page():
    widths: list[int] = []

    def fetch(start: int, end: int) -> list[dict]:
        width = end - start + 1
        widths.append(width)
        if width > 80:
            raise RpcRangeError("too wide")
        return [{"i": start}]

    fetch_logs_adaptive(0, 400, fetch, init_span=200, min_span=20, max_logs=40)
    first_ok = next(index for index, width in enumerate(widths) if width <= 80)
    assert any(width > widths[first_ok] for width in widths[first_ok + 1 :])


def test_batched_transaction_lookups_are_faster_than_one_by_one():
    def transport(url: str, payload: dict | list) -> dict | list:
        time.sleep(0.01)
        if isinstance(payload, list):
            return [
                {"jsonrpc": "2.0", "id": item["id"], "result": {"from": "0x" + "ab" * 20}}
                for item in payload
            ]
        return {"jsonrpc": "2.0", "id": payload["id"], "result": {"from": "0x" + "ab" * 20}}

    client = RpcClient(["https://a.example"], transport=transport, sleep=lambda _seconds: None)
    calls = [("eth_getTransactionByHash", [f"0x{index:064x}"]) for index in range(40)]
    started = time.perf_counter()
    client.batch(calls, batch_size=20)
    batched = time.perf_counter() - started
    started = time.perf_counter()
    for method, params in calls:
        client.call(method, params)
    serial = time.perf_counter() - started
    assert batched < serial / 4, f"batch {batched:.3f}s vs one-by-one {serial:.3f}s"


def test_http_status_is_classified_without_a_network():
    def transport(url: str, payload: dict) -> dict:
        request = httpx.Request("POST", url)
        response = httpx.Response(429, json={"error": "rate"}, request=request)
        response.raise_for_status()
        return {}

    client = RpcClient(["https://a.example"], transport=transport, sleep=lambda _s: None, max_attempts=2)
    try:
        client.call("eth_blockNumber", [])
    except Exception as exc:
        assert "rate" in str(exc).lower() or "429" in str(exc)
    else:
        raise AssertionError("expected the rate limit to fail the call")

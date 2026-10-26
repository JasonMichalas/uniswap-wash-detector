import httpx

from washdetect.rpc import RpcClient, RpcRangeError, RpcRateError, fetch_logs_adaptive


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

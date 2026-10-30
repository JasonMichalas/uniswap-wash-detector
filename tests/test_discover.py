from washdetect.abi import INITIALIZE_V4, PAIR_CREATED, POOL_CREATED, pad_address
from washdetect.config import POOL_BASES, POOL_MANAGER, V2_FACTORY, V3_FACTORY, V4_START_BLOCK
from washdetect.discover import _PoolScan, discover_pools
from washdetect.rpc import RpcClient
from washdetect.store import Store

TOKEN = "0x" + "ab" * 20
ZERO = "0x" + "00" * 32


def _client(log_calls: list, eth_calls: list) -> RpcClient:
    def transport(_url: str, payload: dict | list):
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if item["method"] == "eth_getLogs":
                log_calls.append(item["params"][0])
            elif item["method"] == "eth_call":
                eth_calls.append(item["params"][0])
        return [
            {"jsonrpc": "2.0", "id": item["id"], "result": [] if item["method"] == "eth_getLogs" else ZERO}
            for item in items
        ] if isinstance(payload, list) else {
            "jsonrpc": "2.0",
            "id": payload["id"],
            "result": [] if payload["method"] == "eth_getLogs" else ZERO,
        }

    return RpcClient(
        ["https://rpc.mevblocker.io"],
        transport=transport,
        sleep=lambda _seconds: None,
        max_attempts=2,
    )


def test_a_v4_pool_id_is_kept():
    from washdetect.discover import _add_rows

    pool_id = "0x" + "ab" * 32
    found = []
    _add_rows(
        _client([], []),
        [(pool_id, "v4", "uniswap", TOKEN, "0x" + "11" * 20, 500, "")],
        found,
        set(),
        {},
    )
    assert [pool.pool_address for pool in found] == [pool_id]


def test_one_token_does_not_scan_the_whole_factory():
    logs: list = []
    calls: list = []
    store = Store(":memory:")
    head = V4_START_BLOCK + 20
    pools = discover_pools(_client(logs, calls), TOKEN, store=store, head=head)
    assert pools == []
    assert any(item["to"] == V2_FACTORY for item in calls)
    assert any(item["to"] == V3_FACTORY for item in calls)
    for base in POOL_BASES:
        assert any(base.removeprefix("0x") in item["data"] for item in calls)
    padded = pad_address(TOKEN)
    v4 = [item for item in logs if item["address"] == POOL_MANAGER]
    assert {tuple(item["topics"]) for item in v4} == {
        (INITIALIZE_V4, None, padded),
        (INITIALIZE_V4, None, None, padded),
    }
    for item in logs:
        topics = item["topics"]
        assert topics[0] in {INITIALIZE_V4, PAIR_CREATED, POOL_CREATED}
        assert padded in topics
        assert item["address"] in {POOL_MANAGER, V2_FACTORY, V3_FACTORY}

    again: list = []
    discover_pools(_client(again, []), TOKEN, store=store, head=head)
    assert again == []
    store.close()


def test_both_initialize_filters_share_one_scale():
    notes: list[str] = []
    scan = _PoolScan(V4_START_BLOCK, V4_START_BLOCK + 99, 2, notes.append)
    scan.advance(0, V4_START_BLOCK)
    assert notes[-1].startswith("Finding pools · blocks 1 of 200")
    scan.advance(1, V4_START_BLOCK + 99)
    assert "blocks 101 of 200" in notes[-1]
    scan.advance(0, V4_START_BLOCK + 99)
    assert "blocks 200 of 200" in notes[-1]


def test_a_fresh_initialize_scan_keeps_one_wide_empty_page_per_filter():
    logs: list = []
    notes: list[str] = []
    head = V4_START_BLOCK + 100_000
    store = Store(":memory:")
    discover_pools(_client(logs, []), TOKEN, store=store, head=head, progress=notes.append)
    v4 = [item for item in logs if item["address"] == POOL_MANAGER]
    assert len(v4) == 2
    for item in v4:
        assert int(item["fromBlock"], 16) == V4_START_BLOCK
        assert int(item["toBlock"], 16) == head
    assert any(note.startswith("Finding pools · blocks 0 of ") for note in notes)
    store.close()


def test_a_stale_v4_scan_does_not_hide_a_new_pool():
    """An earlier scan recorded the v4 range while dropping pool ids. The same
    swap window must still read logs for a pool that scan missed."""
    from datetime import datetime

    from eth_abi import encode

    from washdetect.pipeline import _fetch_swap_groups

    head = V4_START_BLOCK + 20
    pool_id = "0x" + "cd" * 32
    quote = "0x" + "11" * 20
    data = "0x" + encode(
        ["uint24", "int24", "address", "uint160", "int24"],
        [3000, 60, "0x" + "00" * 20, 1, 60],
    ).hex()
    init_log = {
        "address": POOL_MANAGER,
        "topics": [INITIALIZE_V4, pool_id, pad_address(TOKEN), pad_address(quote)],
        "data": data,
    }
    logs: list = []

    def transport(_url: str, payload: dict | list):
        items = payload if isinstance(payload, list) else [payload]
        bodies = []
        for item in items:
            if item["method"] == "eth_getLogs":
                logs.append(item["params"][0])
                result = [init_log]
            else:
                result = ZERO
            bodies.append({"jsonrpc": "2.0", "id": item["id"], "result": result})
        return bodies if isinstance(payload, list) else bodies[0]

    client = RpcClient(
        ["https://rpc.mevblocker.io"],
        transport=transport,
        sleep=lambda _seconds: None,
        max_attempts=2,
    )
    store = Store(":memory:")
    pair = "0x" + "22" * 20
    store.save_pools(
        [(TOKEN, pair, "v3", TOKEN, quote, 3000, 18, 18, "TOK", "WETH")]
    )
    store.add_coverage(TOKEN, "direct-quotes", 0, 0)
    store.add_coverage(TOKEN, "v4-currency0", V4_START_BLOCK, head)
    store.add_coverage(TOKEN, "v4-currency1", V4_START_BLOCK, head)
    store.add_coverage(TOKEN, f"swaps:{pair}", 100, 399)
    store.add_coverage(TOKEN, f"liquidity:{pair}", 100, 399)
    found = discover_pools(client, TOKEN, store=store, head=head)
    assert pool_id in {pool.pool_address for pool in found}
    assert logs, "the old v4 coverage was treated as a finished scan"

    class _Logs:
        def __init__(self) -> None:
            self.calls: list[dict] = []
            self._transport = object()
            self.notes: list[str] = []

        def get_logs(self, **kwargs):
            self.calls.append(kwargs)
            on_chunk = kwargs.get("on_chunk")
            if on_chunk:
                on_chunk(kwargs["start_block"], kwargs["end_block"], [])
            return []

        def spawn(self):
            return self

        def log_workers(self, _width=None):
            return 1

        def close(self) -> None:
            return None

    reader = _Logs()
    _fetch_swap_groups(
        store,
        reader,  # type: ignore[arg-type]
        TOKEN,
        found,
        100,
        399,
        300,
        datetime(2026, 10, 6),
        lambda _message: None,
        lambda: False,
    )
    assert reader.calls
    assert all(call["address"] != pair for call in reader.calls)
    asked = reader.calls[0]["topics"][1]
    assert pool_id in asked
    assert store.range_covered(TOKEN, f"swaps:{pool_id}", 100, 399)
    assert store.range_covered(TOKEN, f"liquidity:{pool_id}", 100, 399)
    store.close()


def test_a_short_index_tip_does_not_force_another_full_scan():
    from washdetect.discover import _index_covers
    from washdetect.store import CHAIN

    store = Store(":memory:")
    head = V4_START_BLOCK + 100_000
    store.add_coverage(CHAIN, "v4-initialize", V4_START_BLOCK, head - 10)
    assert _index_covers(store, "v4-initialize", V4_START_BLOCK, head)
    bare = Store(":memory:")
    bare.add_coverage(CHAIN, "v4-initialize", V4_START_BLOCK, V4_START_BLOCK + 5)
    assert _index_covers(bare, "v4-initialize", V4_START_BLOCK, head) is False
    store.close()
    bare.close()


def test_batch_reads_v4_pools_from_the_shared_index():
    from washdetect.config import V2_FACTORY_BLOCK, V3_FACTORY_BLOCK
    from washdetect.store import CHAIN

    head = V4_START_BLOCK + 20
    pool_id = "0x" + "cd" * 32
    quote = "0x" + "11" * 20
    logs: list = []
    store = Store(":memory:")
    store.add_coverage(CHAIN, "v4-initialize", V4_START_BLOCK, head)
    store.add_coverage(CHAIN, "v2-pairs", V2_FACTORY_BLOCK, head)
    store.add_coverage(CHAIN, "v3-pools", V3_FACTORY_BLOCK, head)
    store.upsert_chain_pools([(pool_id, "v4", "uniswap", TOKEN, quote, 500, "0x" + "00" * 20)])
    pools = discover_pools(
        _client(logs, []),
        TOKEN,
        store=store,
        head=head,
        use_v4_index=True,
    )
    assert pool_id in {pool.pool_address for pool in pools}
    assert logs == []
    store.close()


def test_a_quote_pool_skips_the_factory_log_walk():
    from washdetect.abi import calldata

    logs: list = []
    pair = "0x" + "11" * 20
    pair_word = "0x" + pair.removeprefix("0x").rjust(64, "0")
    quoted = {calldata("get_pair")[:10], calldata("get_pool")[:10]}

    def transport(_url: str, payload: dict | list):
        items = payload if isinstance(payload, list) else [payload]
        bodies = []
        for item in items:
            if item["method"] == "eth_getLogs":
                logs.append(item["params"][0])
                result = []
            elif item["params"][0]["data"][:10] in quoted:
                result = pair_word
            else:
                result = "0x" + "0" * 62 + "12"
            bodies.append({"jsonrpc": "2.0", "id": item["id"], "result": result})
        return bodies if isinstance(payload, list) else bodies[0]

    client = RpcClient(
        ["https://rpc.mevblocker.io"],
        transport=transport,
        sleep=lambda _seconds: None,
        max_attempts=2,
    )
    pools = discover_pools(client, TOKEN, store=Store(":memory:"), head=V4_START_BLOCK + 20)
    assert pools
    assert all(item["address"] == POOL_MANAGER for item in logs)
    assert all(item["topics"][0] == INITIALIZE_V4 for item in logs)

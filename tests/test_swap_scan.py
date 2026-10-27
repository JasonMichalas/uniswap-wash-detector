"""Large pool sets must not issue one eth_getLogs per handful of pools."""

from datetime import datetime

from washdetect.config import POOL_MANAGER
from washdetect.discover import PoolMeta
from washdetect.logs import _pool_topics
from washdetect.pipeline import _fetch_one_gap, _fetch_swap_groups, _use_v4_bulk
from washdetect.rpc import RpcError
from washdetect.store import Store

TOKEN = "0x" + "ab" * 20


def _pool(index: int, protocol: str) -> PoolMeta:
    if protocol == "v4":
        address = "0x" + f"{index:064x}"
    else:
        address = "0x" + f"{index:040x}"
    return PoolMeta(
        pool_address=address,
        protocol=protocol,
        token0=TOKEN,
        token1="0x" + "11" * 20,
        fee=3000,
        token0_decimals=18,
        token1_decimals=18,
        token0_symbol="TOK",
        token1_symbol="WETH",
    )


class _Logs:
    def __init__(self, *, limit: int | None = None) -> None:
        self.calls: list[dict] = []
        self._transport = object()
        self.notes: list[str] = []
        self._limit = limit

    def get_logs(self, **kwargs):
        address = kwargs["address"]
        count = len(address) if isinstance(address, list) else 1
        if self._limit is not None and count > self._limit:
            raise RpcError("maximum of 10 addresses is allowed")
        self.calls.append(kwargs)
        on_chunk = kwargs.get("on_chunk")
        if on_chunk:
            on_chunk(kwargs["start_block"], kwargs["end_block"], [])
        return []

    def spawn(self):
        return self

    def close(self) -> None:
        return None


def _scan(store: Store, client: _Logs, pools: list[PoolMeta]) -> None:
    _fetch_one_gap(
        store,
        client,  # type: ignore[arg-type]
        TOKEN,
        pools,
        100,
        399,
        100,
        399,
        datetime(2026, 10, 27),
        lambda _message: None,
        lambda: False,
    )


def test_a_long_window_with_a_few_v4_pools_stays_filtered():
    assert _use_v4_bulk(9, 100_000) is False
    assert _use_v4_bulk(20, 300) is True


def test_many_v4_pools_share_one_poolmanager_scan(tmp_path):
    store = Store(tmp_path / "scan.duckdb")
    pools = [_pool(i, "v4") for i in range(1, 21)]
    client = _Logs()
    _scan(store, client, pools)
    assert len(client.calls) == 1
    assert client.calls[0]["address"] == POOL_MANAGER
    assert client.calls[0]["topics"] == _pool_topics("v4")
    for pool in pools:
        assert store.range_covered(TOKEN, f"swaps:{pool.pool_address}", 100, 399)
    store.close()


def test_a_handful_of_v4_pools_filters_by_pool_id(tmp_path):
    store = Store(tmp_path / "scan.duckdb")
    pools = [_pool(i, "v4") for i in range(1, 5)]
    client = _Logs()
    _scan(store, client, pools)
    assert len(client.calls) == 1
    topics = client.calls[0]["topics"]
    assert topics[0] == _pool_topics("v4")[0]
    assert set(topics[1]) == {pool.pool_address for pool in pools}
    store.close()


def test_many_pools_read_cached_ranges_once(tmp_path):
    store = Store(tmp_path / "scan.duckdb")
    pools = [_pool(i, "v4") for i in range(1, 31)]
    store.add_coverage(TOKEN, f"swaps:{pools[0].pool_address}", 100, 399)
    client = _Logs()
    _fetch_swap_groups(
        store,
        client,  # type: ignore[arg-type]
        TOKEN,
        pools,
        100,
        399,
        300,
        datetime(2026, 10, 27),
        lambda _message: None,
        lambda: False,
    )
    assert len(client.calls) == 1
    assert client.calls[0]["address"] == POOL_MANAGER
    assert store.range_covered(TOKEN, f"swaps:{pools[-1].pool_address}", 100, 399)
    store.close()


def test_address_limit_splits_the_pool_batch(tmp_path):
    store = Store(tmp_path / "scan.duckdb")
    pools = [_pool(i, "v3") for i in range(1, 21)]
    client = _Logs(limit=8)
    _scan(store, client, pools)
    sizes = [len(call["address"]) for call in client.calls]
    assert sizes
    assert max(sizes) <= 8
    assert sum(sizes) == 20
    for pool in pools:
        assert store.range_covered(TOKEN, f"swaps:{pool.pool_address}", 100, 399)
    store.close()

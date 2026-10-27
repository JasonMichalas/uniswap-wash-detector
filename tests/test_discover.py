from washdetect.abi import INITIALIZE_V4, PAIR_CREATED, POOL_CREATED, pad_address
from washdetect.config import POOL_BASES, POOL_MANAGER, V2_FACTORY, V3_FACTORY, V4_START_BLOCK
from washdetect.discover import discover_pools
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

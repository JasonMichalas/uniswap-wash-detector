from washdetect.subgraph import map_subgraph_swap


def test_v3_swap_maps_to_a_sell_of_the_target():
    token = "0x" + "aa" * 20
    row = {
        "id": "1",
        "timestamp": 1_700_000_000,
        "sender": "0x" + "11" * 20,
        "recipient": "0x" + "22" * 20,
        "origin": "0x" + "33" * 20,
        "amount0": "2.5",
        "amount1": "-1",
        "amountUSD": "100.5",
        "logIndex": "4",
        "transaction": {"id": "0x" + "ab" * 32, "blockNumber": 18_000_000},
        "pool": {
            "id": "0x" + "44" * 20,
            "feeTier": "3000",
            "token0": {"id": token, "symbol": "TOK", "decimals": "18"},
            "token1": {"id": "0x" + "55" * 20, "symbol": "WETH", "decimals": "18"},
        },
    }
    mapped = map_subgraph_swap(row, token)
    assert mapped is not None
    assert mapped["side"] == "sell"
    assert mapped["amount_token"] == 2.5
    assert mapped["trader"].endswith("33" * 20)
    assert mapped["usd_value"] == 100.5
    assert mapped["block_number"] == 18_000_000

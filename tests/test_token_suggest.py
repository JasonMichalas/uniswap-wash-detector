"""Token suggestions: Uniswap pools from a mocked GeckoTerminal, and the curated fallback."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from washdetect.api import app
from washdetect.tokensuggest import clear_cache, suggest_tokens

PEPE = "0x6982508145454ce325ddbe47a25d4ec3d2311933"
WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
OTHER = "0x1111111111111111111111111111111111111111"
POOL = "0x2222222222222222222222222222222222222222"
V4_TOKEN = "0x3333333333333333333333333333333333333333"
CURVE_TOKEN = "0x4444444444444444444444444444444444444444"


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_cache()
    yield
    clear_cache()


def _token(token_id: str, address: str, name: str, symbol: str, logo: str | None = None) -> dict:
    return {
        "id": token_id,
        "type": "token",
        "attributes": {"address": address, "name": name, "symbol": symbol, "image_url": logo},
    }


def _pool(pool_address: str, dex: str, base_id: str, quote_id: str, volume: str) -> dict:
    return {
        "id": f"eth_{pool_address}",
        "type": "pool",
        "attributes": {"name": "pair", "address": pool_address, "volume_usd": {"h24": volume}},
        "relationships": {
            "base_token": {"data": {"id": base_id, "type": "token"}},
            "quote_token": {"data": {"id": quote_id, "type": "token"}},
            "dex": {"data": {"id": dex, "type": "dex"}},
        },
    }


def _payload() -> dict:
    pepe_id = f"eth_{PEPE}"
    weth_id = f"eth_{WETH}"
    other_id = f"eth_{OTHER}"
    v4_id = f"eth_{V4_TOKEN}"
    curve_id = f"eth_{CURVE_TOKEN}"
    return {
        "data": [
            _pool(POOL, "uniswap_v3", weth_id, pepe_id, "1500000.5"),
            _pool("0x" + "55" * 20, "uniswap_v2", other_id, weth_id, "10"),
            _pool("0x" + "66" * 20, "uniswap_v3", other_id, weth_id, "99"),
            _pool("0x" + "77" * 20, "uniswap-v4-ethereum", v4_id, weth_id, "9000000"),
            _pool("0x" + "88" * 20, "curve", curve_id, weth_id, "8000000"),
        ],
        "included": [
            _token(pepe_id, PEPE, "Pepe", "PEPE", "https://example.test/pepe.png"),
            _token(weth_id, WETH, "Wrapped Ether", "WETH"),
            _token(other_id, OTHER, "Other", "OTH"),
            _token(v4_id, V4_TOKEN, "Version Four", "V4"),
            _token(curve_id, CURVE_TOKEN, "Curve Token", "CRV"),
        ],
    }


def test_mocked_pools_keep_uniswap_v2_and_v3_token_addresses():
    payload = _payload()

    def client(url: str) -> dict:
        return payload

    body = suggest_tokens(client=client)
    assert body["source"] == "geckoterminal"
    assert "results" not in body
    addresses = {item["address"] for item in body["trending"]}
    assert addresses == {PEPE, OTHER}
    assert POOL not in addresses
    assert V4_TOKEN not in addresses
    assert CURVE_TOKEN not in addresses
    pepe = next(item for item in body["trending"] if item["address"] == PEPE)
    assert pepe["symbol"] == "PEPE"
    assert pepe["logo_url"] == "https://example.test/pepe.png"
    assert pepe["volume_24h_usd"] == 1500000.5
    # The same token in two pools keeps the larger 24h volume.
    other = next(item for item in body["trending"] if item["address"] == OTHER)
    assert other["volume_24h_usd"] == 99
    assert body["trending"][0]["address"] == PEPE


def test_top_volume_includes_a_second_page_when_the_first_is_short():
    extra = "0x" + "99" * 20
    extra_id = f"eth_{extra}"
    page2 = {
        "data": [_pool("0x" + "ab" * 20, "uniswap_v3", extra_id, f"eth_{WETH}", "42")],
        "included": [
            _token(extra_id, extra, "Second Page", "PAGE2"),
            _token(f"eth_{WETH}", WETH, "Wrapped Ether", "WETH"),
        ],
    }

    def client(url: str) -> dict:
        if "page=2" in url:
            return page2
        return _payload()

    body = suggest_tokens(client=client)
    symbols = [item["symbol"] for item in body["top_volume"]]
    assert "PAGE2" in symbols
    assert "PEPE" in symbols
    assert all(item["symbol"] != "PAGE2" for item in body["trending"])


def test_fallback_when_the_api_fails_and_search_matches_the_curated_list():
    def client(url: str) -> dict:
        raise RuntimeError("geckoterminal down")

    body = suggest_tokens("pepe", client=client)
    assert body["source"] == "curated"
    assert body["trending"]
    assert all(item["logo_url"] is None for item in body["trending"])
    assert [item["symbol"] for item in body["results"]] == ["PEPE"]
    assert body["results"][0]["address"] == PEPE


def test_full_address_does_not_call_the_search_endpoint():
    calls: list[str] = []

    def client(url: str) -> dict:
        calls.append(url)
        if "search/pools" in url:
            raise AssertionError(url)
        return {"data": [], "included": []}

    pasted = "0x" + "ab" * 20
    body = suggest_tokens(pasted, client=client)
    assert body["results"] == [
        {"name": pasted, "symbol": "", "address": pasted, "logo_url": None, "volume_24h_usd": None}
    ]
    assert all("search/pools" not in url for url in calls)


def test_search_uses_curated_names_when_the_search_call_fails_but_lists_worked():
    payload = _payload()

    def client(url: str) -> dict:
        if "search/pools" in url:
            raise RuntimeError("search down")
        return payload

    body = suggest_tokens("ens", client=client)
    assert body["source"] == "geckoterminal"
    assert body["trending"][0]["address"] == PEPE
    assert body["results"][0]["symbol"] == "ENS"
    assert body["results"][0]["address"] == "0xc18360217d8f7ab5e7c516566761ea12ce7f9d72"


def test_suggest_route_returns_the_fallback_without_opening_the_database(monkeypatch):
    def explode(*_args, **_kwargs):
        raise AssertionError("the suggestions route opened the database")

    def offline(_url: str) -> dict:
        raise RuntimeError("offline")

    monkeypatch.setattr("washdetect.api.Store", explode)
    monkeypatch.setattr("washdetect.tokensuggest.http_get", offline)
    response = TestClient(app).get("/api/tokens/suggest", params={"q": "pepe"})
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "curated"
    assert body["results"][0]["address"] == PEPE


def test_suggest_route_returns_mocked_trending_tokens(monkeypatch):
    payload = _payload()
    monkeypatch.setattr("washdetect.tokensuggest.http_get", lambda _url: payload)
    response = TestClient(app).get("/api/tokens/suggest")
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "geckoterminal"
    assert {item["address"] for item in body["top_volume"]} == {PEPE, OTHER}

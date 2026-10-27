from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

_API = "https://api.geckoterminal.com/api/v2"
_HEADERS = {"Accept": "application/json", "User-Agent": "washdetect/0.1"}
_TIMEOUT = 8.0
_CACHE_TTL = 300.0
_LIST_CAP = 12

# If one side is a quote asset, the project token is the other side.
_QUOTES = frozenset(
    {
        "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        "0xdac17f958d2ee523a2206206994597c13d831ec7",
        "0x6b175474e89094c44da98b954eedeac495271d0f",
    }
)

CURATED: list[dict[str, Any]] = [
    {"name": "Ethereum Name Service", "symbol": "ENS", "address": "0xc18360217d8f7ab5e7c516566761ea12ce7f9d72"},
    {"name": "Uniswap", "symbol": "UNI", "address": "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984"},
    {"name": "Chainlink", "symbol": "LINK", "address": "0x514910771af9ca656af840dff83e8264ecf986ca"},
    {"name": "Aave", "symbol": "AAVE", "address": "0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9"},
    {"name": "Maker", "symbol": "MKR", "address": "0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2"},
    {"name": "Lido DAO", "symbol": "LDO", "address": "0x5a98fcbea516cf06857215779fd812ca3bef1b32"},
    {"name": "Curve DAO Token", "symbol": "CRV", "address": "0xd533a949740bb3306d119cc777fa900ba034cd52"},
    {"name": "Compound", "symbol": "COMP", "address": "0xc00e94cb662c3520282e6f5717214004a7f26888"},
    {"name": "Sushi", "symbol": "SUSHI", "address": "0x6b3595068778dd592e39a122f4f5a5cf09c90fe2"},
    {"name": "1inch", "symbol": "1INCH", "address": "0x111111111117dc0aa78b770fa6a738034120c302"},
    {"name": "The Graph", "symbol": "GRT", "address": "0xc944e90c64b2c07662a292be6244bdf05cda44a7"},
    {"name": "Balancer", "symbol": "BAL", "address": "0xba100000625a3754423978a60c9317c58a424e3d"},
    {"name": "Synthetix", "symbol": "SNX", "address": "0xc011a73ee8576fb46f5e1c5751ca3b9fe0af2a6f"},
    {"name": "Rocket Pool", "symbol": "RPL", "address": "0xd33526068d116ce69f19a9ee46f0bd304f21a51f"},
    {"name": "ApeCoin", "symbol": "APE", "address": "0x4d224452801aced8b2f0aebe155379bb5d594381"},
    {"name": "Turbo", "symbol": "TURBO", "address": "0xa35923162c49cf95e6bf26623385eb431ad920d3"},
    {"name": "Pepe", "symbol": "PEPE", "address": "0x6982508145454ce325ddbe47a25d4ec3d2311933"},
    {"name": "Shiba Inu", "symbol": "SHIB", "address": "0x95ad61b0a150d79219dcf64e1e6cc01f0b64c4ce"},
    {"name": "Milady", "symbol": "LADYS", "address": "0x12970e6868f88f6557b76120662c1b3e50a646bf"},
    {"name": "Mog Coin", "symbol": "MOG", "address": "0xaaee1a9723aadb7afa2810263653a34ba2c21c7a"},
    {"name": "HarryPotterObamaSonic10Inu", "symbol": "BITCOIN", "address": "0x72e4f9f808c49a2a61de9c5896298920dc4eeea9"},
    {"name": "Cult DAO", "symbol": "CULT", "address": "0xf0f9d895aca5c8678f706fb8216fa22957685a13"},
]

_lock = threading.Lock()
_cache: dict[str, tuple[float, Any]] = {}

Client = Callable[[str], dict[str, Any]]


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def suggest_tokens(query: str = "", client: Client | None = None) -> dict[str, Any]:
    fetch = client or http_get
    source, trending, top = _lists(fetch)
    body: dict[str, Any] = {"source": source, "trending": trending, "top_volume": top}
    text = " ".join((query or "").split())[:64]
    if not text:
        return body
    if _is_address(text):
        body["results"] = [_token_for_address(text)]
        return body
    found, _live = _search(text, fetch)
    if not found:
        found = _filter_curated(text)
    body["results"] = found
    return body


def http_get(url: str) -> dict[str, Any]:
    import httpx

    response = httpx.get(url, headers=_HEADERS, timeout=_TIMEOUT)
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError("GeckoTerminal did not return an object.")
    return body


def _lists(fetch: Client) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    cached = _read("lists")
    if cached is not None:
        return cached["source"], cached["trending"], cached["top_volume"]
    trending = _safe_pools(_TRENDING, fetch)
    top = _top_pools(fetch)
    if trending or top:
        payload = {"source": "geckoterminal", "trending": trending, "top_volume": top}
    else:
        fallback = _curated_rows()[:_LIST_CAP]
        payload = {"source": "curated", "trending": fallback, "top_volume": list(fallback)}
    _write("lists", payload)
    return payload["source"], payload["trending"], payload["top_volume"]


def _search(text: str, fetch: Client) -> tuple[list[dict[str, Any]], bool]:
    key = f"q:{text.lower()}"
    cached = _read(key)
    if cached is not None:
        return cached["rows"], cached["live"]
    url = f"{_API}/search/pools?query={quote(text)}&network=eth&include=base_token,quote_token,dex&page=1"
    try:
        rows = pools_to_tokens(fetch(url))
        live = True
    except Exception:
        rows = []
        live = False
    _write(key, {"rows": rows, "live": live})
    return rows, live


def _safe_pools(url: str, fetch: Client) -> list[dict[str, Any]]:
    try:
        return pools_to_tokens(fetch(url))
    except Exception:
        return []


_TRENDING = f"{_API}/networks/eth/trending_pools?include=base_token,quote_token,dex&page=1"


def _top_pools(fetch: Client) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for page in (1, 2):
        url = (
            f"{_API}/networks/eth/pools?include=base_token,quote_token,dex"
            f"&sort=h24_volume_usd_desc&page={page}"
        )
        for row in _safe_pools(url, fetch):
            current = merged.get(row["address"])
            if current is None or _outranks(row, current):
                merged[row["address"]] = row
        if len(merged) >= _LIST_CAP:
            break
    ranked = sorted(merged.values(), key=lambda item: (item["volume_24h_usd"] is None, -(item["volume_24h_usd"] or 0)))
    return ranked[:_LIST_CAP]


def pools_to_tokens(payload: dict[str, Any], *, limit: int = _LIST_CAP) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    included = _index_tokens(payload.get("included") or [])
    best: dict[str, dict[str, Any]] = {}
    for pool in payload.get("data") or []:
        if not isinstance(pool, dict):
            continue
        rel = pool.get("relationships") or {}
        dex_id = str(((rel.get("dex") or {}).get("data") or {}).get("id") or "")
        if not _readable_uniswap(dex_id):
            continue
        picked = _pick_side(rel, included)
        if picked is None:
            continue
        volume = _volume(pool.get("attributes") or {})
        row = {
            "name": picked["name"] or picked["symbol"] or picked["address"],
            "symbol": picked["symbol"],
            "address": picked["address"],
            "logo_url": picked["logo_url"],
            "volume_24h_usd": volume,
        }
        current = best.get(row["address"])
        if current is None or _outranks(row, current):
            best[row["address"]] = row
    ranked = sorted(best.values(), key=lambda item: (item["volume_24h_usd"] is None, -(item["volume_24h_usd"] or 0)))
    return ranked[:limit]


def _index_tokens(included: list[Any]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for item in included:
        if not isinstance(item, dict) or item.get("type") != "token":
            continue
        address = _address_of(item)
        if address is None:
            continue
        attrs = item.get("attributes") or {}
        logo = attrs.get("image_url")
        found[str(item.get("id"))] = {
            "address": address,
            "name": str(attrs.get("name") or "").strip(),
            "symbol": str(attrs.get("symbol") or "").strip(),
            "logo_url": logo if isinstance(logo, str) and logo else None,
        }
    return found


def _pick_side(rel: dict[str, Any], included: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    base = included.get(str(((rel.get("base_token") or {}).get("data") or {}).get("id") or ""))
    quote = included.get(str(((rel.get("quote_token") or {}).get("data") or {}).get("id") or ""))
    for side in (base, quote):
        if side is not None and side["address"] not in _QUOTES:
            return side
    return None


def _address_of(item: dict[str, Any]) -> str | None:
    attrs = item.get("attributes") or {}
    raw = str(attrs.get("address") or "").strip().lower()
    if _is_address(raw):
        return raw
    ident = str(item.get("id") or "")
    if ident.lower().startswith("eth_"):
        raw = ident[4:].lower()
        if _is_address(raw):
            return raw
    return None


def _volume(attributes: dict[str, Any]) -> float | None:
    raw = attributes.get("volume_usd")
    if isinstance(raw, dict):
        raw = raw.get("h24")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value < 0 or value != value:
        return None
    return value


def _readable_uniswap(dex_id: str) -> bool:
    dex = dex_id.strip().lower().replace("-", "_")
    if not dex:
        return False
    return (
        dex.startswith("uniswap_v2")
        or dex.startswith("uniswap_v3")
        or dex.startswith("uniswap_v4")
    )


def training_tokens(limit: int, client: Client | None = None) -> list[dict[str, Any]]:
    """Trending and top-volume Uniswap tokens, plus the pinned training list."""
    from pathlib import Path

    skip = _QUOTES | {
        "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599",
        "0x4c9edd5852cd905f086c759e8383e09bff1e68b3",
        "0xdc035d45d973e3ec169d2276ddab16f1e407384f",
        "0x" + "00" * 20,
    }
    pinned: list[str] = []
    train = Path("examples/tokens.train.txt")
    if train.is_file():
        for line in train.read_text(encoding="utf-8").splitlines():
            address = line.split("#", 1)[0].strip().lower()
            if _is_address(address) and address not in skip and address not in pinned:
                pinned.append(address)
    found: dict[str, dict[str, Any]] = {}
    fetch = client or http_get
    urls = [
        f"{_API}/networks/eth/trending_pools?include=base_token,quote_token,dex&page={page}"
        for page in range(1, 4)
    ]
    for dex in ("uniswap_v2", "uniswap_v3", "uniswap-v4-ethereum"):
        urls.extend(
            f"{_API}/networks/eth/dexes/{dex}/pools?include=base_token,quote_token,dex"
            f"&sort=h24_volume_usd_desc&page={page}"
            for page in range(1, 11)
        )
    urls.extend(
        f"{_API}/networks/eth/pools?include=base_token,quote_token,dex&sort=h24_volume_usd_desc&page={page}"
        for page in range(1, 9)
    )
    for url in urls:
        if len(found) >= limit:
            break
        try:
            rows = pools_to_tokens(fetch(url), limit=80)
        except Exception:
            continue
        for row in rows:
            address = row["address"].lower()
            if address in skip:
                continue
            current = found.get(address)
            if current is None or _outranks(row, current):
                found[address] = row
        if client is None:
            time.sleep(0.25)
    ranked = sorted(
        found.values(),
        key=lambda item: (item["volume_24h_usd"] is None, -(item["volume_24h_usd"] or 0)),
    )
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for address in pinned:
        seen.add(address)
        ordered.append(found.get(address) or {
            "name": address[:8],
            "symbol": address[:8],
            "address": address,
            "logo_url": None,
            "volume_24h_usd": None,
        })
    for row in ranked:
        address = row["address"].lower()
        if address in seen:
            continue
        seen.add(address)
        ordered.append(row)
        if len(ordered) >= limit:
            break
    return ordered[:limit]


def listed_tokens(limit: int, client: Client | None = None) -> list[dict[str, Any]]:
    """Trending plus top-volume pages, for batch loads. The picker still caps at 12."""
    fetch = client or http_get
    found: dict[str, dict[str, Any]] = {}
    urls = [f"{_API}/networks/eth/trending_pools?include=base_token,quote_token,dex&page=1"]
    urls.extend(
        f"{_API}/networks/eth/pools?include=base_token,quote_token,dex&sort=h24_volume_usd_desc&page={page}"
        for page in range(1, 11)
    )
    for url in urls:
        if len(found) >= limit:
            break
        try:
            rows = pools_to_tokens(fetch(url), limit=50)
        except Exception:
            continue
        for row in rows:
            current = found.get(row["address"])
            if current is None or _outranks(row, current):
                found[row["address"]] = row
        if client is None:
            time.sleep(0.25)
    ranked = sorted(
        found.values(),
        key=lambda item: (item["volume_24h_usd"] is None, -(item["volume_24h_usd"] or 0)),
    )
    return ranked[:limit]


def _outranks(row: dict[str, Any], current: dict[str, Any]) -> bool:
    new = row["volume_24h_usd"]
    old = current["volume_24h_usd"]
    if new is None:
        return False
    if old is None:
        return True
    return new > old


def _is_address(value: str) -> bool:
    text = value.strip().lower()
    return len(text) == 42 and text.startswith("0x") and all(ch in "0123456789abcdef" for ch in text[2:])


def _curated_rows() -> list[dict[str, Any]]:
    return [
        {
            "name": item["name"],
            "symbol": item["symbol"],
            "address": item["address"],
            "logo_url": None,
            "volume_24h_usd": None,
        }
        for item in CURATED
    ]


def _filter_curated(text: str) -> list[dict[str, Any]]:
    needle = text.lower()
    return [
        row
        for row in _curated_rows()
        if needle in row["name"].lower() or needle in row["symbol"].lower() or needle in row["address"]
    ]


def _token_for_address(text: str) -> dict[str, Any]:
    address = text.strip().lower()
    for row in _curated_rows():
        if row["address"] == address:
            return row
    return {"name": address, "symbol": "", "address": address, "logo_url": None, "volume_24h_usd": None}


def _read(key: str) -> Any | None:
    with _lock:
        found = _cache.get(key)
        if found is None:
            return None
        stored_at, payload = found
        if time.monotonic() - stored_at > _CACHE_TTL:
            _cache.pop(key, None)
            return None
        return payload


def _write(key: str, payload: Any) -> None:
    with _lock:
        _cache[key] = (time.monotonic(), payload)

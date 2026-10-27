from __future__ import annotations

from typing import Any

from eth_abi import decode, encode
from eth_utils import keccak

from washdetect.config import ZERO_ADDRESS


def selector(signature: str) -> bytes:
    return keccak(text=signature)[:4]


def topic0(signature: str) -> str:
    return "0x" + keccak(text=signature).hex()


def pad_address(address: str) -> str:
    return "0x" + address.lower().removeprefix("0x").rjust(64, "0")


def topic_address(topic: str) -> str:
    return "0x" + topic.lower().removeprefix("0x")[-40:]


def normalize_address(address: str) -> str:
    text = address.strip().lower()
    hex_part = text.removeprefix("0x")
    if len(hex_part) != 40 or any(ch not in "0123456789abcdef" for ch in hex_part):
        raise ValueError(f"not an Ethereum address: {address}")
    return "0x" + hex_part


def is_zero(address: str | None) -> bool:
    if not address:
        return True
    try:
        return normalize_address(address) == ZERO_ADDRESS
    except ValueError:
        return True


SIG_GET_PAIR = "getPair(address,address)"
SIG_GET_POOL = "getPool(address,address,uint24)"
SIG_TOKEN0 = "token0()"
SIG_TOKEN1 = "token1()"
SIG_DECIMALS = "decimals()"
SIG_SYMBOL = "symbol()"
SIG_FEE = "fee()"
SIG_SLOT0 = "slot0()"
SIG_BALANCE_OF = "balanceOf(address)"
SIG_AGGREGATE3 = "aggregate3((address,bool,bytes)[])"

SWAP_V2 = topic0("Swap(address,uint256,uint256,uint256,uint256,address)")
MINT_V2 = topic0("Mint(address,uint256,uint256)")
BURN_V2 = topic0("Burn(address,uint256,uint256,address)")
SWAP_V3 = topic0("Swap(address,address,int256,int256,uint160,uint128,int24)")
MINT_V3 = topic0("Mint(address,address,int24,int24,uint128,uint256,uint256)")
BURN_V3 = topic0("Burn(address,int24,int24,uint128,uint256,uint256)")
TRANSFER = topic0("Transfer(address,address,uint256)")

_SEL = {
    "get_pair": selector(SIG_GET_PAIR),
    "get_pool": selector(SIG_GET_POOL),
    "token0": selector(SIG_TOKEN0),
    "token1": selector(SIG_TOKEN1),
    "decimals": selector(SIG_DECIMALS),
    "symbol": selector(SIG_SYMBOL),
    "fee": selector(SIG_FEE),
    "slot0": selector(SIG_SLOT0),
    "balance_of": selector(SIG_BALANCE_OF),
    "aggregate3": selector(SIG_AGGREGATE3),
}


def calldata(name: str, types: list[str] | None = None, args: list[Any] | None = None) -> str:
    raw = _SEL[name]
    if types:
        raw += encode(types, args or [])
    return "0x" + raw.hex()


def decode_address_word(data: str) -> str:
    hex_part = data.removeprefix("0x")
    if len(hex_part) < 40:
        return ZERO_ADDRESS
    return normalize_address(hex_part[-40:])


def _bytes(data: str) -> bytes:
    hex_part = data.removeprefix("0x")
    if len(hex_part) % 2:
        hex_part = "0" + hex_part
    return bytes.fromhex(hex_part) if hex_part else b""


def decode_uint(data: str) -> int:
    raw = _bytes(data)
    if not raw:
        return 0
    return int(decode(["uint256"], raw)[0])


def decode_symbol(data: str) -> str:
    raw = _bytes(data)
    if not raw:
        return ""
    try:
        text = decode(["string"], raw)[0]
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        return str(text).strip("\x00").strip()
    except Exception:
        if len(raw) >= 32:
            return raw[:32].decode("utf-8", errors="replace").strip("\x00").strip()
        return ""


def decode_slot0_sqrt(data: str) -> int:
    raw = _bytes(data)
    # slot0: sqrtPriceX96, tick, then observation and fee fields we ignore.
    sqrt_price, *_rest = decode(
        ["uint160", "int24", "uint16", "uint16", "uint16", "uint8", "bool"], raw
    )
    return int(sqrt_price)


def decode_v2_swap_amounts(data: str) -> tuple[int, int, int, int]:
    raw = _bytes(data)
    a0_in, a1_in, a0_out, a1_out = decode(["uint256", "uint256", "uint256", "uint256"], raw)
    return int(a0_in), int(a1_in), int(a0_out), int(a1_out)


def decode_v3_swap_amounts(data: str) -> tuple[int, int]:
    raw = _bytes(data)
    amount0, amount1, *_rest = decode(
        ["int256", "int256", "uint160", "uint128", "int24"], raw
    )
    return int(amount0), int(amount1)


def decode_v2_mint_amounts(data: str) -> tuple[int, int]:
    a0, a1 = decode(["uint256", "uint256"], _bytes(data))
    return int(a0), int(a1)


def decode_v3_mint_amounts(data: str) -> tuple[int, int]:
    # sender is not indexed on v3 Mint, so it sits in the data.
    _sender, _amount, a0, a1 = decode(["address", "uint128", "uint256", "uint256"], _bytes(data))
    return int(a0), int(a1)


def decode_v3_burn_amounts(data: str) -> tuple[int, int]:
    _amount, a0, a1 = decode(["uint128", "uint256", "uint256"], _bytes(data))
    return int(a0), int(a1)


def decode_transfer_value(data: str) -> int:
    if not data or data == "0x":
        return 0
    return int(decode(["uint256"], _bytes(data))[0])


def encode_balance_of(wallet: str) -> bytes:
    return _SEL["balance_of"] + encode(["address"], [wallet])


def encode_aggregate3(calls: list[tuple[str, bool, bytes]]) -> str:
    body = encode(["(address,bool,bytes)[]"], [calls])
    return "0x" + (_SEL["aggregate3"] + body).hex()


def decode_aggregate3(data: str) -> list[tuple[bool, bytes]]:
    rows = decode(["(bool,bytes)[]"], _bytes(data))[0]
    return [(bool(ok), bytes(ret)) for ok, ret in rows]


def human_amount(raw: int, decimals: int) -> float:
    return raw / float(10**decimals)


def classify_leg(
    token: str,
    token0: str,
    token1: str,
    dec0: int,
    dec1: int,
    amount0_signed: int,
    amount1_signed: int,
) -> dict[str, Any] | None:
    token = token.lower()
    token0 = token0.lower()
    token1 = token1.lower()
    if token == token0:
        raw, dec, quote_raw, quote, qdec = amount0_signed, dec0, amount1_signed, token1, dec1
    elif token == token1:
        raw, dec, quote_raw, quote, qdec = amount1_signed, dec1, amount0_signed, token0, dec0
    else:
        return None
    if raw == 0:
        return None
    side = "sell" if raw > 0 else "buy"
    return {
        "side": side,
        "amount_token": human_amount(abs(raw), dec),
        "amount_quote": human_amount(abs(quote_raw), qdec),
        "quote_token": quote,
        "amount0_raw": str(amount0_signed),
        "amount1_raw": str(amount1_signed),
    }

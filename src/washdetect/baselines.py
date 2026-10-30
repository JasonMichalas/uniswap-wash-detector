"""Published baselines: Victor & Weintraud zero-net sets, and Cong et al. token-level trade-size tests."""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

from washdetect.behaviour import BENFORD
from washdetect.config import hop_addresses
from washdetect.events import SwapEvent, TransferEvent


def _components(edges: dict[str, set[str]]) -> list[set[str]]:
    """Strongly connected components (iterative Tarjan)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on: set[str] = set()
    out: list[set[str]] = []
    counter = 0
    for root in list(edges):
        if root in index:
            continue
        work = [(root, iter(edges.get(root, ())))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on.add(root)
        while work:
            node, it = work[-1]
            nxt = next(it, None)
            if nxt is not None:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on.add(nxt)
                    work.append((nxt, iter(edges.get(nxt, ()))))
                elif nxt in on:
                    low[node] = min(low[node], index[nxt])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                part = set()
                while True:
                    top = stack.pop()
                    on.discard(top)
                    part.add(top)
                    if top == node:
                        break
                out.append(part)
    return out


def zero_net_sets(
    swaps: list[SwapEvent], transfers: list[TransferEvent], pools: set[str], window: int = 7200, margin: float = 0.01
) -> set[tuple[str, int]]:
    """Victor & Weintraud (2021) adapted to AMMs: wallets in one strongly connected transfer component
    (or a single wallet) whose summed buys and sells in a window net to within 1%."""
    skip = {p.lower() for p in pools} | hop_addresses()
    edges: dict[str, set[str]] = defaultdict(set)
    for t in transfers:
        if t.src not in skip and t.dst not in skip and t.src != t.dst:
            edges[t.src].add(t.dst)
            edges.setdefault(t.dst, set())
    owner = {w: i for i, part in enumerate(_components(edges)) for w in part}
    sets: dict[tuple, list[SwapEvent]] = defaultdict(list)
    for s in swaps:
        sets[(owner.get(s.wallet, s.wallet), s.block // window)].append(s)
    flagged: set[tuple[str, int]] = set()
    for rows in sets.values():
        buy = sum(s.amount for s in rows if s.side == "buy")
        sell = sum(s.amount for s in rows if s.side == "sell")
        if buy > 0 and sell > 0 and abs(buy - sell) <= margin * max(buy, sell):
            flagged.update((s.tx_hash, s.log_index) for s in rows)
    return flagged


def size_tests(sizes: list[float]) -> dict[str, float]:
    """Cong et al. (2023): Benford first-digit fit, clustering at round sizes, and the power-law tail exponent."""
    x = np.asarray([v for v in sizes if v and v > 0], dtype=float)
    if len(x) < 30:
        return {"n": len(x), "benford_mad": math.nan, "round_ratio": math.nan, "tail_alpha": math.nan}
    digits = (x / 10 ** np.floor(np.log10(x))).astype(int).clip(1, 9)
    seen = np.bincount(digits, minlength=10)[1:] / len(x)
    scale = 10 ** (np.floor(np.log10(x)) - 1)
    round_share = float((np.abs(np.round(x / scale) * scale - x) <= 0.001 * x).mean())
    expected = 90 * math.log(1.001 / 0.999) / math.log(10)
    tail = np.sort(x)[::-1][: max(10, len(x) // 10)]
    alpha = 1 / np.mean(np.log(tail[:-1] / tail[-1]))
    return {
        "n": len(x),
        "benford_mad": float(np.abs(seen - BENFORD).mean()),
        "round_ratio": round_share / expected,
        "tail_alpha": float(alpha),
    }

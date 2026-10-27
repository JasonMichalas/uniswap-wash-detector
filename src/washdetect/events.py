from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SwapEvent:
    wallet: str
    block: int
    side: str
    amount: float
    usd: float
    tx_hash: str
    log_index: int
    pool: str = ""


@dataclass
class TransferEvent:
    src: str
    dst: str
    block: int
    amount: float
    tx_hash: str
    log_index: int


@dataclass
class Cycle:
    cycle_id: str
    size: int
    kind: str
    wallets: tuple[str, ...]
    volume_usd: float
    confidence: float
    start_block: int
    end_block: int
    swap_ids: list[tuple[str, int]] = field(default_factory=list)
    token_amount: float = 0.0
    pattern_type: str = ""  # editor filter; a wallet that buys and sells is counted in both sets
    n_buyers: int = 0
    n_sellers: int = 0
    n_wallets: int = 0


@dataclass
class WalletWash:
    wallet: str
    confidence: float
    wash_volume_usd: float
    round_trip_count: int
    ring_count: int
    swap_count: int
    net_ratio: float
    volume_usd: float


@dataclass
class Detection:
    cycles: list[Cycle]
    wallets: list[WalletWash]
    flagged: dict[tuple[str, int], tuple[float, str]]

    @property
    def wash_volume_usd(self) -> float:
        return self._wash_volume

    @property
    def total_volume_usd(self) -> float:
        return self._total_volume

    _wash_volume: float = 0.0
    _total_volume: float = 0.0

    @property
    def wash_pct(self) -> float:
        if self._total_volume <= 0:
            return 0.0
        return 100.0 * self._wash_volume / self._total_volume

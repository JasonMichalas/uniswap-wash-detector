from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from washdetect.features import build_wallet_table
from washdetect.store import Store


@dataclass
class TokenData:
    """A cache read. It does not score wash trading."""

    address: str
    start_block: int
    end_block: int
    start_time: datetime
    end_time: datetime
    swaps: pd.DataFrame
    transfers: pd.DataFrame
    wallets: pd.DataFrame
    pools: pd.DataFrame
    pool_addresses: set[str]

    @classmethod
    def load(
        cls,
        store: Store,
        address: str,
        start_block: int,
        end_block: int,
        start_time: datetime,
        end_time: datetime,
    ) -> TokenData:
        token = address.lower()
        swaps = store.read_swaps(token, start_block, end_block)
        transfers = store.read_transfers(token, start_block, end_block)
        balances = store.latest_balances(token, end_block)
        pools = pd.DataFrame(store.pools_for(token))
        addresses = set(pools["pool_address"].astype(str)) if not pools.empty else set()
        wallets = build_wallet_table(
            swaps,
            transfers,
            balances,
            end_ts=end_time,
            window_start_ts=start_time,
            pools=addresses,
        )
        return cls(
            address=token,
            start_block=start_block,
            end_block=end_block,
            start_time=start_time,
            end_time=end_time,
            swaps=swaps,
            transfers=transfers,
            wallets=wallets,
            pools=pools,
            pool_addresses=addresses,
        )

    def frames(self) -> dict[str, object]:
        return {
            "swaps": self.swaps,
            "transfers": self.transfers,
            "wallets": self.wallets,
            "pools": self.pools,
            "pool_addresses": self.pool_addresses,
        }

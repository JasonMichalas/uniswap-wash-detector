from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from washdetect.ranges import covers, merge_intervals, missing_intervals

CHAIN = "__chain__"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
    token VARCHAR PRIMARY KEY,
    symbol VARCHAR,
    decimals INTEGER
);

CREATE TABLE IF NOT EXISTS pools (
    token VARCHAR,
    pool_address VARCHAR,
    protocol VARCHAR,
    token0 VARCHAR,
    token1 VARCHAR,
    fee INTEGER,
    token0_decimals INTEGER,
    token1_decimals INTEGER,
    token0_symbol VARCHAR,
    token1_symbol VARCHAR,
    PRIMARY KEY (token, pool_address)
);

CREATE TABLE IF NOT EXISTS blocks (
    block_number BIGINT PRIMARY KEY,
    ts TIMESTAMP
);

CREATE TABLE IF NOT EXISTS swaps (
    token VARCHAR,
    tx_hash VARCHAR,
    log_index INTEGER,
    block_number BIGINT,
    block_timestamp TIMESTAMP,
    pool_address VARCHAR,
    protocol VARCHAR,
    sender VARCHAR,
    recipient VARCHAR,
    trader VARCHAR,
    side VARCHAR,
    amount_token DOUBLE,
    amount_quote DOUBLE,
    quote_token VARCHAR,
    usd_value DOUBLE,
    amount0_raw VARCHAR,
    amount1_raw VARCHAR,
    PRIMARY KEY (token, tx_hash, log_index)
);

CREATE TABLE IF NOT EXISTS transfers (
    token VARCHAR,
    tx_hash VARCHAR,
    log_index INTEGER,
    block_number BIGINT,
    block_timestamp TIMESTAMP,
    from_address VARCHAR,
    to_address VARCHAR,
    amount_raw VARCHAR,
    amount DOUBLE,
    PRIMARY KEY (token, tx_hash, log_index)
);

CREATE TABLE IF NOT EXISTS liquidity_events (
    token VARCHAR,
    tx_hash VARCHAR,
    log_index INTEGER,
    block_number BIGINT,
    block_timestamp TIMESTAMP,
    pool_address VARCHAR,
    protocol VARCHAR,
    kind VARCHAR,
    sender VARCHAR,
    amount0_raw VARCHAR,
    amount1_raw VARCHAR,
    PRIMARY KEY (token, tx_hash, log_index)
);

CREATE TABLE IF NOT EXISTS coverage (
    token VARCHAR,
    dataset VARCHAR,
    start_block BIGINT,
    end_block BIGINT
);

CREATE TABLE IF NOT EXISTS tx_senders (
    tx_hash VARCHAR PRIMARY KEY,
    sender VARCHAR
);

CREATE TABLE IF NOT EXISTS wallet_balance_daily (
    token VARCHAR,
    wallet VARCHAR,
    day VARCHAR,
    block_number BIGINT,
    balance DOUBLE,
    PRIMARY KEY (token, wallet, day)
);

CREATE TABLE IF NOT EXISTS wallet_balances_end (
    token VARCHAR,
    block_number BIGINT,
    wallet VARCHAR,
    balance DOUBLE,
    PRIMARY KEY (token, block_number, wallet)
);

CREATE TABLE IF NOT EXISTS labelled_swaps (
    token VARCHAR,
    start_block BIGINT,
    end_block BIGINT,
    tx_hash VARCHAR,
    log_index INTEGER,
    is_wash BOOLEAN,
    confidence DOUBLE,
    cycle_id VARCHAR,
    PRIMARY KEY (token, start_block, end_block, tx_hash, log_index)
);

CREATE TABLE IF NOT EXISTS chain_pools (
    pool_address VARCHAR PRIMARY KEY,
    protocol VARCHAR,
    dex VARCHAR,
    token0 VARCHAR,
    token1 VARCHAR,
    fee INTEGER,
    hooks VARCHAR
);

CREATE TABLE IF NOT EXISTS wallet_features (
    token VARCHAR,
    start_block BIGINT,
    end_block BIGINT,
    wallet VARCHAR,
    n_swaps INTEGER,
    n_buys INTEGER,
    n_sells INTEGER,
    buy_amount DOUBLE,
    sell_amount DOUBLE,
    net_position_change DOUBLE,
    round_trip_count INTEGER,
    trade_interval_cv DOUBLE,
    median_inter_swap_blocks DOUBLE,
    n_pools INTEGER,
    counterparty_degree INTEGER,
    funding_source VARCHAR,
    funding_source_degree INTEGER,
    wash_volume_usd DOUBLE,
    volume_usd DOUBLE,
    heuristic_label INTEGER,
    heuristic_confidence DOUBLE,
    PRIMARY KEY (token, start_block, end_block, wallet)
);
"""


class Store:
    """The editor only reads this file. CLI detect and batch are what write labels."""

    def __init__(self, path: str | Path = "data/washdetect.duckdb") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(self.path)
        self.lock = threading.RLock()
        self.con.execute(SCHEMA)

    def close(self) -> None:
        with self.lock:
            self.con.close()

    def _df(self, sql: str, params: list[Any] | None = None) -> pd.DataFrame:
        return self.con.execute(sql, params or []).df()

    def upsert_token(self, token: str, symbol: str, decimals: int) -> None:
        with self.lock:
            self.con.execute(
                "INSERT OR REPLACE INTO tokens VALUES (?, ?, ?)",
                [token, symbol, decimals],
            )

    def token_meta(self, token: str) -> dict[str, Any] | None:
        with self.lock:
            rows = self.con.execute(
                "SELECT symbol, decimals FROM tokens WHERE token = ?", [token]
            ).fetchall()
        if not rows:
            return None
        return {"symbol": rows[0][0], "decimals": rows[0][1]}

    def save_pools(self, rows: list[tuple[Any, ...]]) -> None:
        if not rows:
            return
        with self.lock:
            self.con.executemany(
                "INSERT OR REPLACE INTO pools VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def pools_for(self, token: str) -> list[dict[str, Any]]:
        with self.lock:
            frame = self._df("SELECT * FROM pools WHERE token = ? ORDER BY protocol, pool_address", [token])
        return frame.to_dict(orient="records")

    def coverage(self, token: str, dataset: str) -> list[tuple[int, int]]:
        with self.lock:
            rows = self.con.execute(
                "SELECT start_block, end_block FROM coverage WHERE token = ? AND dataset = ?",
                [token, dataset],
            ).fetchall()
        return [(int(a), int(b)) for a, b in rows]

    def missing(self, token: str, dataset: str, start: int, end: int) -> list[tuple[int, int]]:
        return missing_intervals(self.coverage(token, dataset), start, end)

    def range_covered(self, token: str, dataset: str, start: int, end: int) -> bool:
        return covers(self.coverage(token, dataset), start, end)

    def add_coverage(self, token: str, dataset: str, start: int, end: int) -> None:
        if start > end:
            return
        with self.lock:
            current = [
                (int(a), int(b))
                for a, b in self.con.execute(
                    "SELECT start_block, end_block FROM coverage WHERE token = ? AND dataset = ?",
                    [token, dataset],
                ).fetchall()
            ]
            merged = merge_intervals([*current, (start, end)])
            self.con.execute(
                "DELETE FROM coverage WHERE token = ? AND dataset = ?", [token, dataset]
            )
            self.con.executemany(
                "INSERT INTO coverage VALUES (?, ?, ?, ?)",
                [(token, dataset, a, b) for a, b in merged],
            )

    def insert_swaps(self, rows: list[tuple[Any, ...]]) -> None:
        if not rows:
            return
        with self.lock:
            self.con.executemany(
                "INSERT OR REPLACE INTO swaps VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def insert_transfers(self, rows: list[tuple[Any, ...]]) -> None:
        if not rows:
            return
        with self.lock:
            self.con.executemany(
                "INSERT OR REPLACE INTO transfers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def insert_liquidity(self, rows: list[tuple[Any, ...]]) -> None:
        if not rows:
            return
        with self.lock:
            self.con.executemany(
                "INSERT OR REPLACE INTO liquidity_events VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def remember_blocks(self, rows: list[tuple[int, datetime]]) -> None:
        if not rows:
            return
        with self.lock:
            self.con.executemany("INSERT OR REPLACE INTO blocks VALUES (?, ?)", rows)

    def known_blocks(self, numbers: list[int]) -> dict[int, datetime]:
        if not numbers:
            return {}
        with self.lock:
            self.con.execute("CREATE TEMP TABLE IF NOT EXISTS _nums (n BIGINT)")
            self.con.execute("DELETE FROM _nums")
            self.con.executemany("INSERT INTO _nums VALUES (?)", [(n,) for n in numbers])
            rows = self.con.execute(
                "SELECT b.block_number, b.ts FROM blocks b JOIN _nums u ON b.block_number = u.n"
            ).fetchall()
        return {int(n): ts for n, ts in rows}

    def remember_senders(self, rows: list[tuple[str, str]]) -> None:
        if not rows:
            return
        with self.lock:
            self.con.executemany("INSERT OR REPLACE INTO tx_senders VALUES (?, ?)", rows)

    def known_senders(self, tx_hashes: list[str]) -> dict[str, str]:
        if not tx_hashes:
            return {}
        with self.lock:
            self.con.execute("CREATE TEMP TABLE IF NOT EXISTS _txs (h VARCHAR)")
            self.con.execute("DELETE FROM _txs")
            self.con.executemany("INSERT INTO _txs VALUES (?)", [(h,) for h in tx_hashes])
            rows = self.con.execute(
                "SELECT s.tx_hash, s.sender FROM tx_senders s JOIN _txs t ON s.tx_hash = t.h"
            ).fetchall()
        return {h: sender for h, sender in rows}

    def apply_traders(self, token: str, rows: list[tuple[str, str]]) -> None:
        if not rows:
            return
        with self.lock:
            self.con.executemany(
                "UPDATE swaps SET trader = ? WHERE token = ? AND tx_hash = ? AND trader IS NULL",
                [(sender, token, tx) for tx, sender in rows],
            )

    def apply_prices(self, token: str, rows: list[tuple[float, str, int]]) -> None:
        if not rows:
            return
        with self.lock:
            self.con.executemany(
                "UPDATE swaps SET usd_value = ? WHERE token = ? AND tx_hash = ? AND log_index = ?",
                [(usd, token, tx, log_index) for usd, tx, log_index in rows],
            )

    def swaps_missing_trader(self, token: str, start: int, end: int) -> list[str]:
        with self.lock:
            rows = self.con.execute(
                """
                SELECT DISTINCT tx_hash FROM swaps
                WHERE token = ? AND block_number BETWEEN ? AND ? AND trader IS NULL
                """,
                [token, start, end],
            ).fetchall()
        return [r[0] for r in rows]

    def swaps_missing_price(self, token: str, start: int, end: int) -> pd.DataFrame:
        with self.lock:
            return self._df(
                """
                SELECT tx_hash, log_index, amount_token, amount_quote, quote_token, side
                FROM swaps
                WHERE token = ? AND block_number BETWEEN ? AND ? AND usd_value IS NULL
                """,
                [token, start, end],
            )

    def read_swaps(self, token: str, start: int, end: int) -> pd.DataFrame:
        with self.lock:
            return self._df(
                """
                SELECT * FROM swaps
                WHERE token = ? AND block_number BETWEEN ? AND ?
                ORDER BY block_number, log_index
                """,
                [token, start, end],
            )

    def read_transfers(self, token: str, start: int, end: int) -> pd.DataFrame:
        with self.lock:
            return self._df(
                """
                SELECT * FROM transfers
                WHERE token = ? AND block_number BETWEEN ? AND ?
                ORDER BY block_number, log_index
                """,
                [token, start, end],
            )

    def count_swaps(self, token: str, start: int, end: int) -> int:
        with self.lock:
            return int(
                self.con.execute(
                    "SELECT COUNT(*) FROM swaps WHERE token = ? AND block_number BETWEEN ? AND ?",
                    [token, start, end],
                ).fetchone()[0]
            )

    def count_transfers(self, token: str, start: int, end: int) -> int:
        with self.lock:
            return int(
                self.con.execute(
                    """
                    SELECT COUNT(*) FROM transfers
                    WHERE token = ? AND block_number BETWEEN ? AND ?
                    """,
                    [token, start, end],
                ).fetchone()[0]
            )

    def priced_swap_count(self, token: str, start: int, end: int) -> int:
        with self.lock:
            return int(
                self.con.execute(
                    """
                    SELECT COUNT(*) FROM swaps
                    WHERE token = ? AND block_number BETWEEN ? AND ? AND usd_value IS NOT NULL
                    """,
                    [token, start, end],
                ).fetchone()[0]
            )

    def distinct_traders(self, token: str, start: int, end: int, limit: int) -> list[str]:
        with self.lock:
            rows = self.con.execute(
                """
                SELECT trader FROM swaps
                WHERE token = ? AND block_number BETWEEN ? AND ? AND trader IS NOT NULL
                GROUP BY trader
                ORDER BY COUNT(*) DESC
                LIMIT ?
                """,
                [token, start, end, limit],
            ).fetchall()
        return [r[0] for r in rows]

    def top_receivers(self, token: str, start: int, end: int, limit: int) -> list[str]:
        with self.lock:
            rows = self.con.execute(
                """
                SELECT to_address FROM transfers
                WHERE token = ? AND block_number BETWEEN ? AND ?
                GROUP BY to_address
                ORDER BY SUM(amount) DESC
                LIMIT ?
                """,
                [token, start, end, limit],
            ).fetchall()
        return [r[0] for r in rows]

    def save_balances(self, token: str, block: int, rows: list[tuple[str, float]]) -> None:
        if not rows:
            return
        with self.lock:
            self.con.executemany(
                "INSERT OR REPLACE INTO wallet_balances_end VALUES (?, ?, ?, ?)",
                [(token, block, wallet, balance) for wallet, balance in rows],
            )

    def balances_at(self, token: str, block: int) -> dict[str, float]:
        with self.lock:
            rows = self.con.execute(
                "SELECT wallet, balance FROM wallet_balances_end WHERE token = ? AND block_number = ?",
                [token, block],
            ).fetchall()
        return {w: float(b) for w, b in rows}

    def latest_balances(self, token: str, end_block: int) -> dict[str, float]:
        with self.lock:
            row = self.con.execute(
                """
                SELECT MAX(block_number) FROM wallet_balances_end
                WHERE token = ? AND block_number <= ?
                """,
                [token, end_block],
            ).fetchone()
            if not row or row[0] is None:
                return {}
            return self.balances_at(token, int(row[0]))

    def replace_daily_balances(self, token: str, rows: list[tuple[Any, ...]]) -> None:
        with self.lock:
            self.con.execute("DELETE FROM wallet_balance_daily WHERE token = ?", [token])
            if rows:
                self.con.executemany(
                    "INSERT INTO wallet_balance_daily VALUES (?, ?, ?, ?, ?)",
                    rows,
                )

    def save_labels(
        self,
        token: str,
        start: int,
        end: int,
        flagged: list[tuple[str, int, bool, float, str | None]],
        features: pd.DataFrame,
    ) -> None:
        with self.lock:
            self.con.execute(
                "DELETE FROM labelled_swaps WHERE token = ? AND start_block = ? AND end_block = ?",
                [token, start, end],
            )
            self.con.execute(
                "DELETE FROM wallet_features WHERE token = ? AND start_block = ? AND end_block = ?",
                [token, start, end],
            )
            if flagged:
                self.con.executemany(
                    "INSERT INTO labelled_swaps VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        (token, start, end, tx, log_index, is_wash, conf, cycle_id)
                        for tx, log_index, is_wash, conf, cycle_id in flagged
                    ],
                )
            if features is not None and not features.empty:
                records = []
                for row in features.itertuples(index=False):
                    records.append(
                        (
                            token,
                            start,
                            end,
                            row.wallet,
                            int(row.n_swaps),
                            int(row.n_buys),
                            int(row.n_sells),
                            float(row.buy_amount),
                            float(row.sell_amount),
                            float(row.net_position_change),
                            int(row.round_trip_count),
                            None if pd.isna(row.trade_interval_cv) else float(row.trade_interval_cv),
                            None
                            if pd.isna(row.median_inter_swap_blocks)
                            else float(row.median_inter_swap_blocks),
                            int(row.n_pools),
                            int(row.counterparty_degree),
                            row.funding_source or "",
                            int(row.funding_source_degree),
                            float(row.wash_volume_usd),
                            float(row.volume_usd),
                            int(row.heuristic_label),
                            float(row.heuristic_confidence),
                        )
                    )
                self.con.executemany(
                    "INSERT INTO wallet_features VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    records,
                )

    def upsert_chain_pools(self, rows: list[tuple[Any, ...]]) -> None:
        if not rows:
            return
        with self.lock:
            self.con.executemany(
                "INSERT OR REPLACE INTO chain_pools VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def chain_pools_for(self, token: str) -> list[dict[str, Any]]:
        with self.lock:
            frame = self._df(
                "SELECT * FROM chain_pools WHERE token0 = ? OR token1 = ? ORDER BY protocol, pool_address",
                [token, token],
            )
        return frame.to_dict(orient="records")

    def export_features(self, token: str, path: str) -> int:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            count = int(
                self.con.execute(
                    "SELECT COUNT(*) FROM wallet_features WHERE token = ?", [token]
                ).fetchone()[0]
            )
            self.con.execute(
                "COPY (SELECT * FROM wallet_features WHERE token = ?) TO ? (FORMAT PARQUET)",
                [token, path],
            )
        return count

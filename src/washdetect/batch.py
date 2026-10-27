from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from washdetect.discover import catch_up_factory_tips, index_v4
from washdetect.pipeline import LoadReport, load_token
from washdetect.rpc import Cancelled, RpcClient
from washdetect.store import Store

LoadFn = Callable[[str, Callable[[], bool]], LoadReport]


class BatchScrape:
    """Fetch many tokens into one cache. A failed token is recorded and the next one runs."""

    def __init__(
        self,
        db_path: str,
        *,
        days: int,
        token_seconds: int,
        include_forks: bool,
        out_dir: str,
    ) -> None:
        self.db_path = db_path
        self.days = days
        self.token_seconds = token_seconds
        self.include_forks = include_forks
        self.out_dir = Path(out_dir)
        self.window = f"days:{days}"

    def run(self, tokens: list[str], *, load: LoadFn | None = None) -> dict[str, int]:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        store = Store(self.db_path)
        counts = {"done": 0, "failed": 0, "partial": 0, "skipped": 0}
        started = time.perf_counter()
        spent: list[float] = []
        try:
            if load is None:
                self._prepare_index()
            pending = []
            for token in tokens:
                status = store.batch_status(token.lower(), self.window)
                if status == "done":
                    counts["skipped"] += 1
                    continue
                pending.append(token.lower())
            print(
                f"Batch {self.window}: {len(tokens)} token(s), {counts['skipped']} already done, "
                f"{len(pending)} to do. One token at a time. "
                f"Each token stops after {self.token_seconds}s.",
                flush=True,
            )
            for index, token in enumerate(pending, start=1):
                eta = ""
                if spent:
                    eta = f" · eta {_hours((sum(spent) / len(spent)) * (len(pending) - index + 1))}"
                print(f"\n=== token {index} of {len(pending)} · {token}{eta}", flush=True)
                t0 = time.perf_counter()
                deadline = t0 + self.token_seconds

                def cancel(deadline: float = deadline) -> bool:
                    return time.perf_counter() >= deadline

                try:
                    if load is None:
                        report = self._load(store, token, cancel)
                    else:
                        report = load(token, cancel)
                except Cancelled:
                    self._record(store, token, "partial", "time limit", None, time.perf_counter() - t0)
                    counts["partial"] += 1
                    print(f"PARTIAL {token}: time limit", flush=True)
                except Exception as exc:
                    self._record(store, token, "failed", str(exc), None, time.perf_counter() - t0)
                    counts["failed"] += 1
                    print(f"FAILED {token}: {exc}", flush=True)
                else:
                    self._record(store, token, "done", "", report, time.perf_counter() - t0)
                    counts["done"] += 1
                    print(
                        f"DONE {report.symbol} pools {report.pools} "
                        f"swaps {report.swaps} transfers {report.transfers} "
                        f"in {report.seconds:.1f}s",
                        flush=True,
                    )
                spent.append(time.perf_counter() - t0)
                self._write_checkpoint(store, counts)
            counts["seconds"] = round(time.perf_counter() - started, 1)
            print(
                f"\nBatch finished: {counts['done']} done, {counts['partial']} partial, "
                f"{counts['failed']} failed, {counts['skipped']} already done, "
                f"{counts['seconds']}s.",
                flush=True,
            )
            return counts
        finally:
            store.close()

    def _prepare_index(self) -> None:
        client = RpcClient()
        store = Store(self.db_path)
        try:
            head = client.block_number()
            print(f"Shared v4 index through block {head:,}", flush=True)
            index_v4(client, store, head, lambda message: print(message, flush=True))
            catch_up_factory_tips(client, store, head, lambda message: print(message, flush=True))
        finally:
            store.close()
            client.close()

    def _load(self, store: Store, token: str, cancel: Callable[[], bool]) -> LoadReport:
        client = RpcClient()
        try:
            head = client.block_number()
            # The index is filled once up front. This only reads blocks mined since the previous token.
            index_v4(client, store, head, lambda _message: None, cancel)
            catch_up_factory_tips(client, store, head, lambda _message: None, cancel)
            return load_token(
                token,
                days=self.days,
                db_path=self.db_path,
                progress=lambda message: print(message, flush=True),
                include_forks=self.include_forks,
                cancel=cancel,
                use_v4_index=True,
                store=store,
                client=client,
            )
        finally:
            client.close()

    def _record(
        self,
        store: Store,
        token: str,
        status: str,
        reason: str,
        report: LoadReport | None,
        seconds: float,
    ) -> None:
        store.save_batch(
            token,
            self.window,
            status,
            reason,
            report.symbol if report else "",
            report.pools if report else 0,
            report.swaps if report else 0,
            report.transfers if report else 0,
            report.seconds if report else seconds,
        )

    def _write_checkpoint(self, store: Store, counts: dict[str, int]) -> None:
        rows = store.batch_rows(self.window)
        payload: dict[str, Any] = {
            "window": self.window,
            "done": [row["token"] for row in rows if row["status"] == "done"],
            "partial": {row["token"]: row["reason"] for row in rows if row["status"] == "partial"},
            "failed": {row["token"]: row["reason"] for row in rows if row["status"] == "failed"},
            "counts": counts,
        }
        path = self.out_dir / "batch-checkpoint.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _hours(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.1f}h"

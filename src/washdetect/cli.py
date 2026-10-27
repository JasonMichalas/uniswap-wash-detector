from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from washdetect.config import DEFAULT_DB, MAX_WINDOW_DAYS
from washdetect.features import build_feature_frame
from washdetect.models import probability_weighted_wash_percent
from washdetect.pipeline import LoadReport, load_token
from washdetect.querylang.engine import QueryEngine
from washdetect.querylang.errors import QueryError
from washdetect.store import Store
from washdetect.token import TokenData


def main(argv: list[str] | None = None) -> int:
    """fetch only fills the cache. detect and query run because they were asked to."""
    parser = argparse.ArgumentParser(
        prog="washdetect",
        description="Uniswap Wash Detector. Ethereum mainnet, Uniswap v2 and v3, no API key required.",
    )
    parser.add_argument("--db", default=DEFAULT_DB, help="DuckDB file (default: data/washdetect.duckdb)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    fetch = sub.add_parser("fetch", help="Cache pools, swaps, and transfers. Does not score wash trading.")
    _window_args(fetch)
    fetch.add_argument("token")
    fetch.add_argument("--rediscover", action="store_true")

    detect = sub.add_parser("detect", help="Load if needed, then print the rule-based wash percentage.")
    _window_args(detect)
    detect.add_argument("token")

    query = sub.add_parser("query", help="Load if needed, then run one query-language statement.")
    _window_args(query)
    query.add_argument("token")
    query.add_argument("query")

    batch = sub.add_parser("batch", help="Fetch each token in a file and write heuristic features.")
    _window_args(batch)
    batch.add_argument("tokens_file")
    batch.add_argument("--out", default="data/training", help="Directory for per-token parquet files")

    train = sub.add_parser(
        "train",
        help="Fit Isolation Forest and XGBoost on cached tokens plus planted wash groups.",
    )
    _window_args(train)
    train.add_argument("--tokens", default="examples/tokens.train.txt", help="Address list, one per line")
    train.add_argument("--report", default="docs/evaluation.md")
    train.add_argument("--model-dir", default="", help="Where to write the model (default: the package artifacts directory)")
    train.add_argument("--seed", type=int, default=0)

    serve = sub.add_parser("serve", help="Run the API and, if built, the node editor.")
    serve.add_argument("--port", type=int, default=8742)
    serve.add_argument("--host", default="0.0.0.0")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "fetch":
            report = _load(args)
            _print_report(report)
            return 0
        if args.cmd == "detect":
            return _detect(args)
        if args.cmd == "query":
            return _query(args)
        if args.cmd == "batch":
            return _batch(args)
        if args.cmd == "train":
            return _train(args)
        if args.cmd == "serve":
            return _serve(args)
    except (QueryError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 1


def _window_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--days", type=int, help=f"Window length ending now, 1 to {MAX_WINDOW_DAYS}")
    parser.add_argument("--start", help="Inclusive start date YYYY-MM-DD")
    parser.add_argument("--end", help="Inclusive end date YYYY-MM-DD")


def _load(args: argparse.Namespace) -> LoadReport:
    return load_token(
        args.token,
        days=args.days,
        start=args.start,
        end=args.end,
        db_path=args.db,
        progress=lambda message: print(message, flush=True),
        rediscover=getattr(args, "rediscover", False),
    )


def _print_report(report: LoadReport) -> None:
    print(
        f"{report.symbol} {report.token}\n"
        f"window {report.start_time} → {report.end_time} "
        f"blocks {report.start_block}–{report.end_block}\n"
        f"pools {report.pools} (v2 {report.v2_pools}, v3 {report.v3_pools})\n"
        f"swaps {report.swaps}  transfers {report.transfers}  priced {report.priced_swaps}\n"
        f"balances {report.balance_wallets}"
        f"{' (capped)' if report.balances_truncated else ''}\n"
        f"fetched {report.blocks_fetched} block-spans, "
        f"{report.blocks_cached} already cached, {report.seconds:.1f}s"
    )
    if report.eth_usd:
        print(f"ETH/USD mark {report.eth_usd:,.2f}")
    if report.notes:
        print("RPC notes:")
        for note in report.notes[:12]:
            print(f"  - {note}")
    print("No wash-trading score was computed.")


def _open(args: argparse.Namespace, report: LoadReport) -> tuple[Store, dict[str, object], datetime, datetime]:
    store = Store(args.db)
    start_ts = datetime.fromisoformat(report.start_time)
    end_ts = datetime.fromisoformat(report.end_time)
    loaded = TokenData.load(store, report.token, report.start_block, report.end_block, start_ts, end_ts)
    return store, loaded.frames(), start_ts, end_ts


def _detect(args: argparse.Namespace) -> int:
    report = _load(args)
    store, frames, start_ts, end_ts = _open(args, report)
    try:
        engine = QueryEngine()
        result = engine.run(
            engine.compile("wash_percent"),
            end_ts=end_ts,
            window_start=start_ts,
            window_end=end_ts,
            **frames,
        )
        detection = result.detection
        value = float(result.outputs[0].rows[0]["value"]) if result.outputs[0].rows else 0.0
        print(f"\nRule-based wash volume: {value:.2f}%")
        if detection is not None:
            kinds: dict[str, int] = {}
            for cycle in detection.cycles:
                kinds[cycle.kind] = kinds.get(cycle.kind, 0) + 1
            print(
                "cycles "
                + ", ".join(f"{kind}={count}" for kind, count in sorted(kinds.items()))
                if kinds
                else "cycles 0"
            )
            flagged = detection.flagged
            swaps = frames["swaps"]
            usd = []
            probs = []
            if swaps is not None and not swaps.empty:
                for row in swaps.itertuples(index=False):
                    price = getattr(row, "usd_value", None)
                    usd.append(0.0 if price is None else float(price))
                    key = (str(row.tx_hash), int(row.log_index))
                    probs.append(1.0 if key in flagged else 0.0)
            interval = probability_weighted_wash_percent(usd, probs)
            print(
                "bootstrap 95% interval of that hard-label ratio: "
                f"[{interval['ci_low']:.2f}%, {interval['ci_high']:.2f}%] "
                f"on {interval['n']} priced swaps"
            )
            print(
                "This interval resamples the rule's 0/1 labels. It is not an "
                "XGBoost probability interval; that lands in milestone 2."
            )
            from washdetect.detector import SwapEvent, TransferEvent

            swap_events = [
                SwapEvent(
                    wallet=str(row.trader or ""),
                    block=int(row.block_number),
                    side=str(row.side),
                    amount=float(row.amount_token or 0),
                    usd=float(row.usd_value or 0),
                    tx_hash=str(row.tx_hash),
                    log_index=int(row.log_index),
                    pool=str(getattr(row, "pool_address", "") or ""),
                )
                for row in swaps.itertuples(index=False)
            ] if swaps is not None and not swaps.empty else []
            transfers = frames["transfers"]
            transfer_events = [
                TransferEvent(
                    src=str(row.from_address),
                    dst=str(row.to_address),
                    block=int(row.block_number),
                    amount=float(row.amount or 0),
                    tx_hash=str(row.tx_hash),
                    log_index=int(row.log_index),
                )
                for row in transfers.itertuples(index=False)
            ] if transfers is not None and not transfers.empty else []
            features = build_feature_frame(
                swap_events, transfer_events, detection, excluded=frames["pool_addresses"]
            )
            flagged_rows = [
                (tx, log_index, True, conf, cid) for (tx, log_index), (conf, cid) in detection.flagged.items()
            ]
            store.save_labels(report.token, report.start_block, report.end_block, flagged_rows, features)
            out_root = Path(getattr(args, "out", None) or (Path(args.db).parent / "training"))
            out = out_root / f"{report.token}.parquet" if out_root.suffix != ".parquet" else out_root
            written = store.export_features(report.token, str(out))
            print(f"wrote {written} feature rows to {out}")
        for warning in result.warnings:
            print(f"warning: {warning}")
        return 0
    finally:
        store.close()


def _query(args: argparse.Namespace) -> int:
    report = _load(args)
    store, frames, start_ts, end_ts = _open(args, report)
    try:
        engine = QueryEngine()
        result = engine.run(
            engine.compile(args.query),
            end_ts=end_ts,
            window_start=start_ts,
            window_end=end_ts,
            **frames,
        )
        print(result.query)
        for output in result.outputs:
            print(f"\n# {output.title}")
            if output.kind == "number":
                value = output.rows[0]["value"] if output.rows else None
                suffix = "%" if output.format == "percent" else ""
                print(f"{value}{suffix}")
                if output.note:
                    print(output.note)
            else:
                columns = output.columns
                print("\t".join(columns))
                for row in output.rows:
                    print("\t".join("" if row[col] is None else str(row[col]) for col in columns))
                if output.truncated:
                    print(f"... truncated to {len(output.rows)} rows")
        return 0
    finally:
        store.close()


def _batch(args: argparse.Namespace) -> int:
    path = Path(args.tokens_file)
    tokens = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.split("#", 1)[0].strip()
        if text:
            tokens.append(text)
    if not tokens:
        raise ValueError(f"{path} has no token addresses.")
    out = Path(args.out)
    for token in tokens:
        print(f"\n=== {token}")
        ns = argparse.Namespace(**vars(args), token=token)
        code = _detect(ns)
        if code != 0:
            return code
        print(f"features directory: {out}")
    return 0


def _train(args: argparse.Namespace) -> int:
    from washdetect.dataset import assemble_from_events, events_from_frames
    from washdetect.models import default_model_dir
    from washdetect.train import train_tables

    path = Path(args.tokens)
    tokens = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.split("#", 1)[0].strip()
        if text:
            tokens.append(text)
    if not tokens:
        raise ValueError(f"{path} has no token addresses.")
    tables = []
    failures: list[str] = []
    cold_seconds = _cold_fetch_seconds(args.db)
    for token in tokens:
        print(f"\n=== {token}", flush=True)
        try:
            report = load_token(
                token,
                days=args.days,
                start=args.start,
                end=args.end,
                db_path=args.db,
                progress=lambda message: print(message, flush=True),
            )
            store = Store(args.db)
            try:
                start_ts = datetime.fromisoformat(report.start_time)
                end_ts = datetime.fromisoformat(report.end_time)
                frames = TokenData.load(
                    store, report.token, report.start_block, report.end_block, start_ts, end_ts
                ).frames()
            finally:
                store.close()
            swaps, transfers = events_from_frames(frames["swaps"], frames["transfers"])
            if not swaps:
                print("no swaps in this window; skipping")
                failures.append(f"{token}: no swaps")
                continue
            tables.append(
                assemble_from_events(
                    report.token,
                    report.symbol or token,
                    swaps,
                    transfers,
                    excluded=set(frames["pool_addresses"]),
                    seconds=cold_seconds.get(report.token.lower(), report.seconds),
                    start_block=report.start_block,
                    end_block=report.end_block,
                )
            )
            print(
                f"assembled {report.symbol}: {len(swaps)} real swaps, "
                f"{len(transfers)} transfers, fetch {report.seconds:.1f}s"
            )
        except Exception as exc:
            print(f"skipped {token}: {exc}")
            failures.append(f"{token}: {exc}")
    if len(tables) < 3:
        raise ValueError(
            "Need at least 3 tokens with swaps so train and test do not share a token. "
            + ("; ".join(failures) if failures else "")
        )
    if args.days:
        window = f"Window: the last {args.days} day(s) ending when each token was fetched, Ethereum mainnet, public RPC, no API key."
    else:
        window = f"Window: {args.start} through {args.end} inclusive, Ethereum mainnet, public RPC, no API key."
    window += (
        " Fetch s is the cold public-RPC scrape when a successful row exists in "
        "fetch-log.txt beside the database; otherwise it is the time spent loading "
        "that token during this training run."
    )
    model_dir = Path(args.model_dir) if args.model_dir else default_model_dir()
    report_path = Path(args.report)
    _bundle, _text = train_tables(
        tables,
        seed=args.seed,
        model_dir=model_dir,
        report_path=report_path,
        figure_dir=report_path.parent / "figures",
        window_note=window,
    )
    print(f"\nwrote model to {model_dir}")
    print(f"wrote {report_path}")
    if failures:
        print(f"skipped {len(failures)} token(s)")
    return 0


def _cold_fetch_seconds(db_path: str) -> dict[str, float]:
    path = Path(db_path).expanduser().parent / "fetch-log.txt"
    found: dict[str, float] = {}
    if not path.is_file():
        return found
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 3 or parts[1] != "0":
            continue
        try:
            found[parts[0].lower()] = float(parts[2])
        except ValueError:
            continue
    return found


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    os_db = str(Path(args.db))
    import os

    os.environ["WASHDETECT_DB"] = os_db  # read when the first request opens the store
    print(f"Uniswap Wash Detector on http://127.0.0.1:{args.port}")
    print("Loading a token caches data and leaves the canvas empty.")
    uvicorn.run("washdetect.api:app", host=args.host, port=args.port, reload=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

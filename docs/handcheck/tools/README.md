# Hand-check tools

Scripts used to build the hand-checked set in `docs/handcheck/`. They were run from a scratch
folder (`/workspace/handcheck`), so the paths at the top of each script point there.

1. `detect_all.py` loads each token's cached DuckDB, runs `WashDetector` with the Find wash
   groups defaults (net_ratio 0.05, max_block_gap 7200, max_group_size 24) and writes `<tok>_det.json`.
2. `sample.py` (seed 82) picks 80 flagged groups stratified by rule and confidence band, and
   20 unflagged two-sided wallets as controls (`evidence/sample_*.json`).
3. `evidence.py` collects evidence for each case without using the detector's output. It pulls
   receipts from public RPC, the raw Transfer logs for the window, getCode and nonce, and the
   Etherscan name tag and "Funded By".
4. `summarize.py` prints the readable evidence in `evidence/review_*.txt`, which is what the
   labels were made from.
5. `labels.py` holds the hand labels; `build_csv.py` writes `cases.csv` and the counts.

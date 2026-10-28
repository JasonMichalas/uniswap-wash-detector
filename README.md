# Uniswap Wash Detector

Milestone 2 of an ECM3401 dissertation (University of Exeter). The tool loads Uniswap v2 and v3 swaps from Ethereum mainnet, finds wash groups of every buyer/seller shape, and lets a researcher **build** the analysis in a box editor. A shipped Isolation Forest and a calibrated XGBoost model score wallets and swaps. TreeSHAP says which features moved a wallet's score.

No API key is required. Loading a token never scores wash trading. You press **Run**.

## Try it yourself

Python 3.11, 3.12, and 3.13 all work ([python.org/downloads](https://www.python.org/downloads/)). Node.js 22 or newer works ([nodejs.org](https://nodejs.org/)); this was checked with Node 24.13. Git is the usual install from [git-scm.com](https://git-scm.com/downloads).

Clone the repo, then install the Python package with the ML extra and build the editor. One command starts the API and the built page.

### Windows

```bat
git clone https://github.com/JasonMichalas/uniswap-wash-detector.git
cd uniswap-wash-detector
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -e ".[ml]"
cd frontend
npm ci
npm run build
cd ..
washdetect serve
```

### macOS and Linux

```bash
git clone https://github.com/JasonMichalas/uniswap-wash-detector.git
cd uniswap-wash-detector
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[ml]"
cd frontend
npm ci
npm run build
cd ..
washdetect serve
```

Open [http://127.0.0.1:8742](http://127.0.0.1:8742). `washdetect` is on the PATH only while the virtualenv is active. `python -m washdetect.cli serve` is the same command if the script is not found (`py -3.12 -m washdetect.cli serve` on Windows).

On Windows, `py --list` shows which Pythons are installed. Use whichever of 3.11, 3.12, or 3.13 you have (`py -3.12 -m venv .venv` is the example above). If PowerShell refuses `.venv\Scripts\activate`, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then activate again.

On Debian or Ubuntu, `python3 -m venv` needs the distro package: `sudo apt install python3-venv` (or `python3.12-venv`). macOS and the python.org Windows installer already include it.

### A short first run

1. Click the token box and choose **ENS** from the list (or paste `0xC18360217D8F7Ab5e7c516566761Ea12Ce7F9D72`).
2. The **7d** chip is already selected. For a quicker first fetch, clear it and set a custom window of one day (start and end on the same date). Seven days works too; it just takes longer the first time. Press **Load token**. The status line says **No analysis has been run.**
3. Open **Examples** and choose **Wash volume percent**. That graph is Swaps + Transfers → Find wash groups → Condition `is_wash = true` → Percent of `usd_value` → Number, format percent. Press **Run**. That number is the rule.
4. Open **ML wash volume percent**. Swaps → ML wash score → Percent of `ml_weighted_usd` divided by `usd_value` → Number, format percent. Press **Run**. The card is the probability-weighted wash percent, with a 95% bootstrap interval under the figure.
5. Open **Top 10 wallets by wash probability**, then **Explain the top wallets**. Explain lists the SHAP features that push each wallet's score up or down.

Nothing is drawn until you press Run. Opening an example only places boxes. The canvas starts empty on every page load.

While changing the UI, run the API and Vite together. Vite is on port **8743** and proxies `/api` to 8742:

```bash
washdetect serve
# second terminal, with the same virtualenv available if you call the API yourself
cd frontend && npm run dev
```

The dev editor is [http://127.0.0.1:8743](http://127.0.0.1:8743).

`--db` is a global flag and goes **before** the subcommand. The default file is `data/washdetect.duckdb` (gitignored). `WASHDETECT_DB` overrides it for the server. `WASHDETECT_MODEL_DIR` overrides the shipped model directory.

## What a wash group is

A group is a connected flow inside a block window: pool buys, wallet-to-wallet token transfers, and pool sells, with `sum(buys) ≈ sum(sells)` within a tolerance. The detector labels the shape:

| `pattern_type` | Meaning |
|---|---|
| `round_trip` | One wallet buys and sells about the same amount. |
| `ring` | A cycle, or one buyer and one seller across two or more wallets. |
| `one_to_many` | One buyer, many sellers. |
| `many_to_one` | Many buyers, one seller. A buys 2, B buys 2, both send to C, C sells 4.01. |
| `many_to_many` | Several buyers and several sellers, including a shuffle through intermediate wallets, and groups where some wallets both buy and sell. |

Filterable fields on the cycles stream: `pattern_type`, `n_buyers`, `n_sellers`, `n_wallets` (intermediates count), and `size` (same as `n_wallets`). The example **Groups with at least 3 wallets** filters `n_wallets >= 3` and `pattern_type = many_to_many`.

The Find wash groups box takes three parameters. Defaults are tolerance `0.05`, window `200` blocks, max group size `16`. The hard cap is 64 wallets. A component larger than the cap is skipped whole, so a trimmed subset cannot invent a match. Hubs (more than 25 counterparties), routers, pools, and the zero and burn addresses are not edges.

The older Gan-style pair matcher is still there (`kind` is `self`, `linked_pair`, or `transfer_cycle`). `confidence` is a heuristic in `[0, 1]`, not a probability. A bare match is about 0.50. A tight round-trip can clear `confidence > 0.8`.

## Models

`washdetect train` fits two XGBoost classifiers (wallet and swap) and two Isolation Forests, then writes `docs/evaluation.md`, the plots in `docs/figures/`, and a small model under `src/washdetect/artifacts/`.

```bash
washdetect train --tokens examples/tokens.train.txt --days 2 --report docs/evaluation.md
```

The files in `src/washdetect/artifacts/` were saved with scikit-learn 1.9.1 and XGBoost 3.4.1. The bundle is about 1.8 MB.

The token list mixes large names (ENS, UNI, LINK, AAVE, MKR), mid-caps (LDO, CRV, COMP, SUSHI, 1INCH, GRT, BAL, SNX, RPL, APE), and thinner or newer pools (TURBO, PEPE, SHIB, LADYS, MOG, BITCOIN/hpos10inu, CULT). The split is by token, not by row. Labels are the rule's weak label on real rows, plus synthetic groups planted into the training frame. Planted addresses start with `0x57` and are not written to the chain cache.

The model does **not** see the rule's own outputs: `round_trip_count`, `wash_volume_usd`, `heuristic_label`, `heuristic_confidence`, `pattern_type`, `n_buyers`, `n_sellers`, `n_wallets`. It sees swap counts, log amounts, net position over the window, timing and burstiness, pool count, counterparty degree, shared-funder degree, and how much of a buy was transferred onward (or how much of a sell arrived by transfer). Probabilities are Platt-scaled on a token held out of training. The anomaly score is a percentile rank in `[0, 1]`, higher meaning less like the training bulk. It is not a probability.

Boxes:

- **ML wash score** adds `ml_score`. On swaps it also adds `ml_weighted_usd = ml_score × usd_value`.
- **Anomaly score** adds `anomaly_score`.
- **Explain** lists the top TreeSHAP reasons for up to 25 wallets. Sort and limit first if you want the highest scores explained.
- **Percent of** can divide one field by another. ML wash percent is `ml_weighted_usd` over `usd_value` on the scored swaps. The number card shows the bootstrap interval when those two columns are the ratio.

Short query for the same ML percent:

```bash
washdetect query 0xC18360217D8F7Ab5e7c516566761Ea12Ce7F9D72 --days 1 "ml_wash_percent"
```

The rule equivalent is `wash_percent`. The dissertation tables, including recall by pattern and by `n_buyers` × `n_sellers`, are in [docs/evaluation.md](docs/evaluation.md).

The shipped model was fit on 22 tokens over the two days ending 26 October 2026 (about 20:00–21:30 UTC, public RPC, no API key). Train / calibration / test were 14 / 1 / 7 tokens. On planted swaps in the test tokens the swap model is precision 92.3%, recall 100%, F1 96.0%, PR-AUC 1.000 (644 planted swaps out of 5,029). Recall is 100% for every pattern and every `n_buyers` × `n_sellers` cell in that test set, including many-to-many groups such as 3 buyers × 4 sellers and 5 × 6. That recall is on one shared generator, not on a public ground truth. Agreement with the rule on real test swaps is precision 92.6% and recall 27.6%: the model does not simply recite the rule.

A few test tokens, real swaps only:

| Token | Rule % | ML % | ML 95% CI |
|---|---:|---:|---|
| MKR | 0.4 | 2.1 | 1.8–2.4 |
| CRV | 4.3 | 8.1 | 6.9–9.6 |
| PEPE | 1.5 | 2.8 | 2.5–3.2 |
| SHIB | 2.4 | 7.5 | 6.0–9.5 |
| 1INCH | 0.0 | 1.9 | 1.4–2.5 |

ENS, which was in the training split, is 0.3% by the rule and 5.7% by the model on the two-day window (127 priced swaps, interval 4.8–6.6).

## Architecture

```mermaid
flowchart LR
  editor["React Flow editor<br/>port 8743 in dev"]
  api["FastAPI<br/>washdetect serve :8742"]
  graph["Graph validator<br/>and compiler"]
  lang["Query language<br/>Lark grammar"]
  sql["Read-only SQL<br/>throwaway DuckDB"]
  cache[("DuckDB cache<br/>data/washdetect.duckdb")]
  load["Token loader"]
  rpc["Public Ethereum RPC<br/>no API key"]
  detect["Flow-group detector"]
  models["XGBoost, Isolation Forest,<br/>TreeSHAP"]

  editor -->|"load / compile / run"| api
  api --> graph
  graph --> lang
  lang --> sql
  api --> load
  load --> rpc
  load --> cache
  api --> cache
  sql --> detect
  sql --> models
  cache --> sql
```

Loading a token never calls the detector or the models (`analysis_ran` is false on the load report). The CLI commands `detect`, `query`, `batch`, and `train` do load and then analyse, because those commands ask for a result. Swaps and transfers both come from public RPC.

## Layout

```
src/washdetect/
  rpc.py            public JSON-RPC, rotation, adaptive eth_getLogs
  pipeline.py       window resolution and incremental load
  detector.py       round-trips plus connected flow groups
  flow.py           buyer/seller components inside a block window
  synthetic.py      planted wash groups, kept out of the chain cache
  features.py       wallet and swap columns, rule outputs excluded
  models.py         Isolation Forest, calibrated XGBoost, TreeSHAP
  scoring.py        joins model columns onto a filtered stream
  train.py          token split, fit, report
  evaluate.py       metrics, plots, docs/evaluation.md
  artifacts/        shipped model (json + joblib)
  querylang/        grammar, parser, sandboxed SQL
  graph/            box catalog, validation, compile-to-query
  api.py            HTTP API and the built editor
  cli.py            fetch, detect, query, train, batch, serve
frontend/           Vite + React + React Flow
examples/graphs/    templates the user opens on purpose
examples/tokens.train.txt
docs/evaluation.md  token-held-out metrics and plots
tests/
```

## Command line

```bash
# Cache only. Does not print a wash percentage.
washdetect fetch 0xC18360217D8F7Ab5e7c516566761Ea12Ce7F9D72 --days 1

# Same pipeline, then the rule-based wash percentage and a bootstrap interval.
washdetect detect 0xC18360217D8F7Ab5e7c516566761Ea12Ce7F9D72 --days 1

# One query. Short forms lower to the same program the editor compiles.
washdetect query 0xC18360217D8F7Ab5e7c516566761Ea12Ce7F9D72 --days 1 "wash_percent"
washdetect query 0xC18360217D8F7Ab5e7c516566761Ea12Ce7F9D72 --days 1 "ml_wash_percent"

# Refit the shipped model and rewrite the evaluation report.
washdetect train --tokens examples/tokens.train.txt --days 2 --report docs/evaluation.md

# Custom range. Both dates are inclusive. The span cannot exceed 90 days.
washdetect fetch 0xC18360217D8F7Ab5e7c516566761Ea12Ce7F9D72 --start 2026-10-25 --end 2026-10-26
```

There is no default window. `--days` (1–90) or both `--start` and `--end` is required.

`blocks_fetched` and `blocks_cached` are **sums across each pool's log and the token's transfer log**, not a single chain span. Extending a window fetches the missing blocks and leaves the overlap on disk.

## Query language

```
wash_percent
ml_wash_percent
wash_wallets where confidence > 0.8
top_holders limit 20
holders where held_days > 90
wallets where not sold_in(30d)
cycles where n_wallets >= 3
cycles where pattern_type = "many_to_many"
```

Program form, which is what the graph compiler emits:

```
s = source swaps
m = ml_score s
p = ratio m m of ml_weighted_usd over usd_value
output number p as "ML wash volume" format percent

w = source wallets
scored = ml_score w
ordered = sort scored by ml_score desc
top = limit ordered 10
reasons = explain top
output table reasons as "Why these wallets score high"
```

`find_wash_groups(swaps, transfers, tolerance, window_blocks, max_group_size)` takes the three detector parameters. Omit them to use the defaults. `detect_cycles(...)` is the same function.

SQL identifiers are whitelisted. String literals are quoted by doubling single quotes. A second belt rejects `drop`, `delete`, `insert`, `update`, `alter`, `attach`, `copy`, and `pragma` outside strings and comments. Limits are 1–1000 rows. The browser shows at most 1000.

## Tests

```bash
pip install -e ".[dev,ml]"
ruff check src tests
pytest
cd frontend && npm ci && npm run build
```

GitHub Actions runs the same two jobs (Python 3.11 with the `ml` extra, Node 22).

## Honest limits

- The plants come from one generator, shared in shape across tokens. High recall means the model recognised that generator on a held-out token. It is not proof the model has seen every real wash ring.
- A real wash swap scored above 0.5 counts as a false positive on the planted-only precision. The report also gives agreement with the rule on real swaps.
- The rule and the model can agree because the behaviour is obvious (a buyer who forwards the tokens), not because the model was shown the rule's output column.
- USD uses one end-of-window ETH price for WETH legs. The training window is two days of public RPC, not the token's life. `held_days` and activity outside the window are invisible.
- Isolation Forest is an anomaly rank, not a calibrated probability. SHAP explains the raw XGBoost margin. Platt scaling is monotone, so the sign of a reason is unchanged, but the probability is not a SHAP unit.
- Pool discovery calls `getPair` / `getPool` for WETH, USDC, USDT, and DAI (v3 fees 100 / 500 / 3000 / 10000). Exotic quote tokens are out of scope.
- The trader is `tx.from`. Routers, Permit2, the zero address, and the burn address are excluded from wallet linking.
- Native ETH transfers are invisible to `eth_getLogs`. Linking uses the token's own ERC-20 transfers (one and two hops) plus a shared non-hub funder.
- A wallet that buys and sells inside the window with a small net looks like wash trading even when it is arbitrage. `n_pools` is a feature so the model can separate those. The rule itself does not.
- `held_days` counts from the first inbound transfer **inside the loaded window**. It is a lower bound. A positive balance with no inbound transfer in range sets `acquired_before_window` and leaves `held_days` null.
- `ever_sold` and `days_since_sell` look only at sells in the loaded range.
- Balances use Multicall3 `balanceOf` at the end block, capped at 800 wallets.
- Swaps with no USD price are omitted from USD sums. Mixing stable and WETH pools can distort a percentage because the ETH price is a single end-of-window spot.
- Hubs above 25 counterparties are not edges. Groups above 64 wallets are dropped whole. The flow search also caps how many buys it seeds and how many groups it keeps, so a pathological token cannot hang the request.
- Public RPC is uneven. dRPC's free plan rejects `eth_getLogs` ranges over 10,000 blocks; the client halves the chunk until the call succeeds. Some endpoints answer `eth_blockNumber` and then fail `eth_getLogs`.

## Later

- Recover native ETH funding with archive traces.
- Do not add Uniswap v4 or other DEXes until the mainnet v2/v3 question is measured.
- A small user study of the node editor: can someone build "top holders who never sold" without a template?

## Licence

MIT. See [LICENSE](LICENSE).

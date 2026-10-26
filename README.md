# Uniswap Wash Detector

Milestone 1 of an ECM3401 dissertation (University of Exeter): a machine-learning *framework* for spotting wash trading in Uniswap liquidity pools. This milestone loads Ethereum mainnet data, labels round-trips with a published heuristic, and lets a researcher **build** an analysis instead of being handed a wash percentage.

Uniswap v2 and v3 only. No API key is required. Isolation Forest, XGBoost, and SHAP are stubbed for milestone 2.

## What you can do

1. Enter a token address and **choose a history window every time** (7, 30, 60, 90 days, or a custom range of at most 90 days). Nothing is selected for you.
2. The loader caches pools, swaps, transfers, and balances in DuckDB. A wider window later fetches only the missing blocks.
3. The canvas stays empty. You add boxes and wires (a React Flow editor, not snap-together blocks).
4. The graph compiles to a small query language, and that language compiles to read-only SQL. The query is shown under the canvas. **Run** is the first time anything is scored.

Wash trading here means a wallet buying and selling with itself, or a ring of wallets (A→B→A, A→B→C→A, and longer) whose net position barely changes. The rule follows Gan et al., ACM TOIT 2024: 2–10 swaps inside 200 blocks whose net token change is at most 5% of the larger side, with wallets linked by token transfers within two hops.

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
  detect["Cycle detector<br/>Gan-style heuristic"]

  editor -->|"load / compile / run"| api
  api --> graph
  graph --> lang
  lang --> sql
  api --> load
  load --> rpc
  load --> cache
  api --> cache
  sql --> detect
  cache --> sql
```

Loading a token never calls the detector (`analysis_ran` is false on the load report). The CLI commands `detect`, `query`, and `batch` do load and then analyse, because those commands ask for a result.

Optional accelerator: if `GRAPH_API_KEY` is set, Uniswap v2/v3 subgraphs can fill swap logs. The RPC path works with the variable unset. Transfers always come from RPC.

## Layout

```
src/washdetect/
  rpc.py            public JSON-RPC, rotation, adaptive eth_getLogs
  ranges.py         inclusive block coverage (extend, don't refetch)
  abi.py            event topics and decoders
  prices.py         stablecoin legs and one ETH/USD spot
  store.py          DuckDB tables
  discover.py       getPair / getPool for WETH, USDC, USDT, DAI
  pipeline.py       window resolution and incremental load
  snapshot.py       frames for one loaded window
  detector.py       round-trips, linked pairs, transfer cycles
  features.py       per-wallet columns for the later models
  models.py         Isolation Forest / XGBoost / SHAP stubs
  querylang/        grammar, parser, sandboxed SQL
  graph/            box catalog, validation, compile-to-query
  api.py            HTTP API and the built editor
  cli.py            fetch, detect, query, batch, serve
frontend/           Vite + React + React Flow
examples/graphs/    templates the user opens on purpose
tests/              parser, SQL, cycles, RPC chunking, graphs
```

## Setup

Python 3.11 or newer, and Node 22 if you want the editor.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

cd frontend
npm ci
npm run build
cd ..
```

`eth-hash[pycryptodome]` is a real dependency: `eth_utils.keccak` needs a hashing backend.

## Run the editor

```bash
washdetect serve
```

That listens on `0.0.0.0:8742` and serves `frontend/dist`. Open [http://127.0.0.1:8742](http://127.0.0.1:8742).

While changing the UI, run the API and the Vite dev server together. Vite is on port **8743** and proxies `/api` to 8742:

```bash
washdetect serve
# second terminal
cd frontend && npm run dev
```

The dev editor is [http://127.0.0.1:8743](http://127.0.0.1:8743).

### Using the canvas

- Paste a token address. Click **7d**, **30d**, **60d**, **90d**, or fill both custom dates (end inclusive, span ≤ 90). **Load token** stays disabled until both the address and a window are set.
- The status line after a load says **No analysis has been run.** Counts are inventory only.
- Click or drag a box from the palette. Wires only connect when the socket types match. A loop in the *graph* is rejected; wash cycles are found by the Cycle detector box.
- Wash percent is not a box. It is Swaps + Transfers → Cycle detector → Condition `is_wash = true` → Percent of `usd_value` against all swaps → Number, format percent. **Examples** (closed until you open it) includes that graph.
- Opening an example only places nodes. You still press **Run**.
- Save downloads the graph as JSON. Open reads a file back. The canvas starts empty on every page load.
- Changing the graph, or loading a token, clears the previous result so a stale percentage cannot linger.
- The ML wash score box type-checks and then refuses to run. No model is trained in this milestone.

`--db` is a global flag and goes **before** the subcommand. The default file is `data/washdetect.duckdb` (gitignored). `WASHDETECT_DB` overrides it for the server.

## Command line

```bash
# Cache only. Does not print a wash percentage.
washdetect fetch 0xC18360217D8F7Ab5e7c516566761Ea12Ce7F9D72 --days 3

# Same pipeline, then the rule-based wash percentage, a bootstrap interval, and a parquet file.
washdetect detect 0xC18360217D8F7Ab5e7c516566761Ea12Ce7F9D72 --days 3

# One query. The short forms lower to the same program the editor compiles.
washdetect query 0xC18360217D8F7Ab5e7c516566761Ea12Ce7F9D72 --days 7 "wash_percent"

# Custom range. Both dates are inclusive. The span cannot exceed 90 days.
washdetect fetch 0xC18360217D8F7Ab5e7c516566761Ea12Ce7F9D72 --start 2026-10-01 --end 2026-10-15

# Many tokens. One address per line, # comments allowed.
washdetect batch examples/tokens.example.txt --days 7 --out data/training
```

There is no default window. `--days` (1–90) or both `--start` and `--end` is required.

`blocks_fetched` and `blocks_cached` are **sums across each pool's log and the token's transfer log**, not a single chain span. Nine pools plus transfers over a 21,539-block window reports about 215,390 block-spans. Extending 7 days to 30 days fetches the older gap and any new head blocks, and leaves the overlap on disk.

## Query language

Short questions the parser accepts:

```
wash_percent
wash_wallets where confidence > 0.8
top_holders limit 20
holders where held_days > 90
wallets where not sold_in(30d)
cycles where size >= 3
```

They desugar to the program form the graph compiler emits, for example:

```
n_swaps1 = source swaps
n_transfers1 = source transfers
n_detect1 = detect_cycles(n_swaps1, n_transfers1)
n_detect1_swaps = project n_detect1 swaps
n_filter1 = filter n_detect1_swaps where is_wash = true
n_ratio1 = ratio n_filter1 n_swaps1 of usd_value
output number n_ratio1 as "Wash volume" format percent
```

SQL identifiers are whitelisted. String literals are quoted by doubling single quotes. A second belt rejects `drop`, `delete`, `insert`, `update`, `alter`, `attach`, `copy`, and `pragma` outside strings and comments. Limits are 1–1000 rows. The browser shows at most 1000.

`confidence` is a heuristic score in `[0, 1]`, not a probability. A bare match of the Gan rule is about 0.50. A tight round-trip scores higher and can clear `confidence > 0.8`.

## Tests

```bash
ruff check src tests
pytest
cd frontend && npm run build
```

GitHub Actions runs the same two jobs (Python 3.11, Node 22).

## Verification on a real token

Fetched with public RPC only (`GRAPH_API_KEY` unset) on 26 October 2026.

| | |
|---|---|
| Token | ENS `0xC18360217D8F7Ab5e7c516566761Ea12Ce7F9D72` |
| Window | 3 days, 2026-10-23 18:54 → 2026-10-26 18:54 |
| Blocks | 26,257,248–26,278,786 |
| Pools | 9 (2 v2, 7 v3), quotes WETH / USDC / USDT / DAI |
| Swaps | 223, all priced |
| Transfers | 1,369 |
| ETH/USD | 2,313.25, Uniswap v3 USDC/WETH 0.05% `slot0` at the window end |
| Fetch | **108.7s**. 0 blocks already cached |
| Rule-based wash volume | **0.15%** (1 self round-trip, 3 transfer cycles) |
| Bootstrap 95% interval | [0.00%, 0.44%] on 223 priced swaps |

The interval resamples the rule's hard 0/1 labels. It is not an ML confidence interval.

RPC limit hit: dRPC's free plan rejects `eth_getLogs` ranges over 10,000 blocks (`400` / `ranges over 10000 blocks are not supported on free plan`). The client halved 4,000 → 2,000 → 1,000 → 500 → 250 → 125 → **62** blocks and finished. A few minutes later, `detect` on the same token only fetched the new head (blocks 26,278,787–26,278,806) and re-priced in **4.8s**. The overlap was not downloaded again.

## Honest limits

- Pool discovery calls `getPair` / `getPool` for WETH, USDC, USDT, and DAI (v3 fees 100 / 500 / 3000 / 10000). Exotic quote tokens are out of scope. A token with none of those pools fails with a clear error.
- The trader is `tx.from`. Swap events often show a router. Routers, Permit2, the zero address, and the burn address are excluded from wallet linking.
- Native ETH transfers are invisible to `eth_getLogs`. Gan et al. linked wallets with ETH transfers within two hops. This baseline uses the token's own ERC-20 transfers (one and two hops) plus a shared non-hub funder. That will miss some rings and can link wallets Gan would not.
- A wallet that buys and sells inside 200 blocks with a small net looks like wash trading even when it is arbitrage. `n_pools` is stored so a later model can separate those. The rule itself does not.
- `held_days` counts from the first inbound transfer **inside the loaded window**. It is a lower bound. A positive balance with no inbound transfer in range sets `acquired_before_window` and leaves `held_days` null. It is not invented.
- `ever_sold` and `days_since_sell` look only at sells in the loaded range.
- Balances use Multicall3 `balanceOf` at the end block, capped at 800 wallets (most active traders, then top receivers, routers excluded).
- USD prefers a stable leg (USDC, USDT, DAI). Otherwise the amount is WETH times **one** ETH/USD spot at the end of the window. A constant ETH price cancels inside a wash percentage computed entirely on WETH pools. Mixing stable and WETH pools can distort it. Swaps with no price are omitted from USD sums.
- If the subgraph returns any swaps for a pool, that pool's whole window is marked covered. Pagination stops at 40,000 rows, so a busy pool can be truncated. Leave `GRAPH_API_KEY` unset to force RPC. Quiet subgraph pools (zero rows) are not marked covered.
- Hubs with more than 25 counterparties do not glue unrelated traders together. The detector caps edges, DFS visits, and reported cycles so a pathological token cannot hang the request.
- The editor's group-by chart is a CSS bar chart of daily USD, with flagged wash volume stacked when the stream has it.

## Milestone 2 and after

- Fit an Isolation Forest on `FEATURE_COLUMNS` (counts, net position, round-trip count, inter-swap timing, counterparty degree, funding source).
- Train XGBoost on the heuristic's weak labels plus synthetic wash rings. Calibrate it.
- Explain scores with SHAP.
- Show the probability-weighted wash percentage and its bootstrap interval on the number card. `probability_weighted_wash_percent` is already implemented; the ML box raises until a model file exists.
- Evaluate precision and recall on synthetic rings, compare with the Gan rule, and write up a few real tokens.
- Recover native ETH funding with archive traces. Do not add Uniswap v4 or other DEXes until the mainnet v2/v3 question is measured.
- A small user study of the node editor: can someone build "top holders who never sold" without a template?

## Licence

MIT. See [LICENSE](LICENSE).

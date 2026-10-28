import { Fragment, useEffect, useMemo, useState, type PointerEvent as ReactPointerEvent } from "react";
import type { GraphError, OutputResult, RunResult } from "../types";

export function Results({
  query,
  sql,
  run,
  errors,
  problemText,
  runError,
  checking,
  tab,
  onTab,
  onFocusNode,
  height,
  collapsed,
  onCollapsed,
  onHeight,
  onOpenResults,
}: {
  query: string;
  sql: string;
  run: RunResult | null;
  errors: GraphError[];
  problemText: (error: GraphError) => string;
  runError: string | null;
  checking: boolean;
  tab: "query" | "sql" | "result";
  onTab: (tab: "query" | "sql" | "result") => void;
  onFocusNode: (id: string) => void;
  height: number;
  collapsed: boolean;
  onCollapsed: (collapsed: boolean) => void;
  onHeight: (height: number) => void;
  onOpenResults: () => void;
}) {
  const [full, setFull] = useState(false);

  function openTab(next: "query" | "sql" | "result") {
    onCollapsed(false);
    onTab(next);
  }

  function startResize(event: ReactPointerEvent<HTMLDivElement>) {
    event.preventDefault();
    const startY = event.clientY;
    const start = height;
    function move(ev: PointerEvent) {
      const max = Math.round(window.innerHeight * 0.85);
      onHeight(Math.min(max, Math.max(160, start + (startY - ev.clientY))));
    }
    function up() {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  const sectionClass = collapsed ? "bottom collapsed" : full ? "bottom full" : "bottom";

  return (
    <section className={sectionClass} style={collapsed || full ? undefined : { height }}>
      {!collapsed && (
        <div
          className="resize-handle"
          role="separator"
          aria-orientation="horizontal"
          aria-label="Resize the results panel"
          onPointerDown={startResize}
        />
      )}
      <div className="tabs">
        <button type="button" className={tab === "query" ? "on" : ""} onClick={() => openTab("query")}>
          Query
        </button>
        <button type="button" className={tab === "sql" ? "on" : ""} onClick={() => openTab("sql")}>
          SQL
        </button>
        <button
          type="button"
          className={tab === "result" ? "on" : ""}
          onClick={() => {
            openTab("result");
            onOpenResults();
          }}
        >
          Result
        </button>
        <span className="tab-note">
          {checking ? "Checking…" : errors.length ? `${errors.length} problem${errors.length === 1 ? "" : "s"}` : "Ready"}
        </span>
        <button type="button" className="text" onClick={() => setFull((value) => !value)}>
          {full ? "Exit full screen" : "Full screen"}
        </button>
        <button type="button" className="text" onClick={() => onCollapsed(!collapsed)}>
          {collapsed ? "Show panel" : "Hide panel"}
        </button>
      </div>
      {!collapsed && errors.length > 0 && (
        <ul className="problems">
          {errors.map((error, index) => (
            <li key={`${error.node_id ?? ""}-${error.edge_id ?? ""}-${index}`}>
              {error.node_id ? (
                <button type="button" onClick={() => onFocusNode(error.node_id!)}>
                  {problemText(error)}
                </button>
              ) : (
                problemText(error)
              )}
            </li>
          ))}
        </ul>
      )}
      {!collapsed && tab === "query" && (
        <pre className="code">{query || "Add a Show box and the question you built appears here."}</pre>
      )}
      {!collapsed && tab === "sql" && (
        <pre className="code">{sql || run?.sql || "SQL appears here after you press Run."}</pre>
      )}
      {!collapsed && tab === "result" && (
        <p className="muted pad">
          {run
            ? "Results open in their own window. Show results brings that window back."
            : runError || "Nothing has been run. Loading a token does not calculate anything."}
        </p>
      )}
    </section>
  );
}

export function resultRowCount(run: RunResult | null): number | null {
  if (!run) return null;
  return run.outputs.reduce((sum, output) => sum + output.rows.length, 0);
}

export function resultsWindowLabel(start: string, end: string): string {
  const a = new Date(start);
  const b = new Date(end);
  if (Number.isNaN(a.getTime())) return "";
  const day = (date: Date) => String(date.getUTCDate());
  const month = (date: Date) => date.toLocaleString("en-GB", { month: "short", timeZone: "UTC" });
  if (Number.isNaN(b.getTime()) || (a.getUTCFullYear() === b.getUTCFullYear() && a.getUTCMonth() === b.getUTCMonth() && a.getUTCDate() === b.getUTCDate())) {
    return `${day(a)} ${month(a)}`;
  }
  if (a.getUTCFullYear() === b.getUTCFullYear() && a.getUTCMonth() === b.getUTCMonth()) {
    return `${day(a)}-${day(b)} ${month(a)}`;
  }
  return `${day(a)} ${month(a)}–${day(b)} ${month(b)}`;
}

export function ResultsModal({
  open,
  onClose,
  run,
  runError,
  noSwaps,
  symbol,
  windowLabel,
}: {
  open: boolean;
  onClose: () => void;
  run: RunResult | null;
  runError: string | null;
  noSwaps: boolean;
  symbol: string;
  windowLabel: string;
}) {
  const [full, setFull] = useState(false);
  const [tab, setTab] = useState(0);
  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  useEffect(() => {
    setTab(0);
  }, [run]);
  if (!open) return null;
  const outputs = run?.outputs ?? [];
  const current = outputs[Math.min(tab, Math.max(outputs.length - 1, 0))];
  const titleWhen = windowLabel ? `, ${windowLabel}` : "";
  const title = `Results: ${symbol || "this token"}${titleWhen}`;
  return (
    <div className="modal-back" role="presentation">
      <section className={full ? "modal full" : "modal"} role="dialog" aria-modal="true" aria-label={title}>
        <header className="modal-bar">
          <h2>{title}</h2>
          <button type="button" className="text" onClick={() => setFull((value) => !value)}>
            {full ? "Exit full screen" : "Full screen"}
          </button>
          {current && current.rows.length > 0 && (
            <button type="button" className="text" onClick={() => downloadCsv(current.title, current.columns, current.rows)}>
              Export CSV
            </button>
          )}
          <button type="button" className="modal-close" onClick={onClose} aria-label="Close results">
            ×
          </button>
        </header>
        {outputs.length > 1 && (
          <div className="modal-tabs">
            {outputs.map((output, index) => (
              <button key={`${output.title}-${index}`} type="button" className={index === tab ? "on" : ""} onClick={() => setTab(index)}>
                {output.title || `Result ${index + 1}`}
              </button>
            ))}
          </div>
        )}
        <div className="modal-body">
          {runError && <p className="banner error">{runError}</p>}
          <ResultBody run={run} runError={null} noSwaps={noSwaps} symbol={symbol} only={current} />
        </div>
      </section>
    </div>
  );
}

function ResultBody({
  only,
  ...rest
}: {
  run: RunResult | null;
  runError: string | null;
  noSwaps: boolean;
  symbol: string;
  only?: OutputResult;
}) {
  return <ResultStack {...rest} only={only} />;
}

function ResultStack({
  run,
  runError,
  noSwaps,
  symbol,
  only,
}: {
  run: RunResult | null;
  runError: string | null;
  noSwaps: boolean;
  symbol: string;
  only?: OutputResult;
}) {
  if (runError) return <p className="banner error">{runError}</p>;
  if (!run) return <p className="muted pad">Nothing has been run. Loading a token does not calculate anything.</p>;
  const outputs = only ? [only] : run.outputs;
  return (
    <div className="result-stack">
      {noSwaps && (
        <p className="banner warn">No swaps were loaded for this window, so this is not a wash result.</p>
      )}
      {run.warnings.map((warning) => (
        <p key={warning} className="banner warn">
          {warning}
        </p>
      ))}
      {outputs.map((output, index) => (
        <OutputView key={`${output.title}-${index}`} output={output} noSwaps={noSwaps} symbol={symbol} />
      ))}
    </div>
  );
}

function OutputView({ output, noSwaps, symbol }: { output: OutputResult; noSwaps: boolean; symbol: string }) {
  if (output.kind === "number") return <NumberCard output={output} />;
  if (output.kind === "chart") return <ChartCard output={output} />;
  return <TableCard output={output} noSwaps={noSwaps} symbol={symbol} />;
}

function NumberCard({ output }: { output: OutputResult }) {
  const raw = output.rows[0]?.value;
  return (
    <article className="number-card">
      <h3>{output.title}</h3>
      <p>{formatNumber(raw, output.format)}</p>
      {output.note && <p className="hint">{output.note}</p>}
    </article>
  );
}

const COLUMN_LABELS: Record<string, string> = {
  why: "Why",
  path: "Path",
  rules: "Rules",
  is_wash: "Wash",
  cycle_id: "Group",
  tx_hash: "Transaction",
  wallet: "Wallet",
  wallets: "Wallets",
  wallets_used: "Wallets used",
  flow: "Flow",
  confidence: "Confidence",
  day: "Day",
  count: "Swaps",
  sum_usd_value: "USD volume",
  sum_amount_token: "Token amount",
  sum_wash_usd: "Flagged USD",
  pool_address: "Pool",
  sender: "Sender",
  recipient: "Recipient",
  trader: "Trader",
  from_address: "From",
  to_address: "To",
  side: "Side",
  amount_token: "Token amount",
  amount_quote: "Quote amount",
  quote_token: "Quote",
  usd_value: "USD",
  price_source: "Price source",
  block_number: "Block",
  block_timestamp: "Time",
  log_index: "Log",
  protocol: "Protocol",
  ml_score: "Score",
  ml_weighted_usd: "Weighted USD",
  token: "Token",
  wash_volume_usd: "Wash volume",
  round_trip_count: "Round trips",
  ring_count: "Circles",
  net_ratio: "Net ratio",
  volume_usd: "Volume",
  swap_count: "Swaps",
  buy_volume_usd: "Buy volume",
  sell_volume_usd: "Sell volume",
  pattern_type: "Pattern",
  n_buyers: "Buyers",
  n_sellers: "Sellers",
  n_wallets: "Wallets in the group",
  size: "Size",
  kind: "Kind",
  start_block: "Start block",
  end_block: "End block",
  balance: "Balance at end of window",
  holder_kind: "Kind",
  held_days: "Days held",
  ever_sold: "Ever sold",
  days_since_sell: "Days since a sell",
  share_percent: "Share",
  realised_usd: "Realised profit",
  unrealised_usd: "Unrealised profit",
  total_usd: "Total profit",
  cost_status: "Cost",
  tokens_bought: "Bought",
  tokens_sold: "Sold",
  attacker: "Attacker",
  victim: "Victim",
  direction: "Direction",
  profit_usd: "Profit",
  buy_tx: "Buy transaction",
  victim_tx: "Victim transaction",
  sell_tx: "Sell transaction",
  received_amount: "Received",
  creator: "Creator",
  source: "Source",
  wallet_count: "Wallets",
  holdings: "Holdings",
  span_minutes: "Minutes",
  first_block: "First block",
  last_block: "Last block",
  removed_percent: "Removed",
  removed_usd: "Dollars removed",
  remover: "Remover",
  original_adder: "Original adder",
  same_wallet: "Same wallet",
  amount_usd: "Dollars",
  supply_percent: "Share of supply",
  tag: "Tag",
  buy_usd: "Bought",
  sell_usd: "Sold",
  net_usd: "Net",
  price_usd: "Price",
  label: "Label",
  reason: "Reason",
  trade_count: "Trades",
  back_to_back_percent: "Back to back",
  active_hours: "Hours active",
  wash_percent: "Wash share",
};

const COLUMN_TIPS: Record<string, string> = {
  why: "Plain-English reason this swap was flagged.",
  path: "Wallets, transactions, and blocks in the group.",
  rules: "Which wash rules matched.",
  is_wash: "Whether this swap belongs to a wash group.",
  cycle_id: "The wash group this row belongs to.",
  tx_hash: "The transaction. Open it on Etherscan or copy the full hash.",
  wallet: "The wallet that stands for this wash group. Often the buyer, or the wallet with the most wash volume.",
  wallets: "Every wallet in this wash group.",
  wallets_used: "How many wallets this one person or controller used.",
  flow: "The buys, transfers, and sells in this group.",
  confidence: "How tightly the buys and sells match. 100% is an exact match.",
  wash_volume_usd: "Dollars of this wallet's swaps that the rules flagged.",
  round_trip_count: "Times this wallet bought and sold with itself.",
  ring_count: "Circles this wallet sat in, where tokens passed around and came back.",
  net_ratio: "How far the buys and sells are from cancelling. 0% means they match.",
  volume_usd: "Dollars traded in the window, flagged or not.",
  swap_count: "How many swaps this wallet made.",
  buy_volume_usd: "Dollars this wallet spent buying the token.",
  sell_volume_usd: "Dollars this wallet received selling the token.",
  usd_value: "Dollar value of this swap.",
  sum_usd_value: "Dollar value added up.",
  sum_wash_usd: "Flagged dollar value added up.",
  ml_weighted_usd: "Model score times the dollar value.",
  amount_token: "How much of the loaded token moved.",
  amount_quote: "How much of the other token moved.",
  price_source: "Where the dollar price came from: a stablecoin, ETH, a quote pool, or other swaps.",
  trader: "The wallet that sent the transaction.",
  sender: "The address the pool recorded as the swap sender. Often a router.",
  recipient: "The address that received the tokens.",
  from_address: "The wallet the tokens moved from.",
  to_address: "The wallet the tokens moved to.",
  side: "Buy or sell on a swap. Winner, Loser, or Unknown on a profit row.",
  pool_address: "The pool the swap traded in.",
  pattern_type: "The shape of the group: a round trip, a fan, or a circle.",
  n_buyers: "How many wallets bought in this group.",
  n_sellers: "How many wallets sold in this group.",
  n_wallets: "How many wallets are in this group.",
  size: "How many swaps are in this group.",
  kind: "Whether the group is a round trip, a passed-along lot, or a circle.",
  ml_score: "The model's wash score, from 0% to 100%.",
  balance: "Tokens this wallet held at the end of the window.",
  share_percent: "This wallet's balance divided by the balances we could read.",
  realised_usd: "Dollars from sells minus the average cost of buys in this window.",
  unrealised_usd: "Tokens still held, at the last trade price, minus the average buy cost.",
  total_usd: "Realised plus unrealised. Blank when the cost is unknown.",
  cost_status: "Known uses buys in this window. Unknown means the earlier price is not here.",
  tokens_bought: "Tokens bought in this window.",
  tokens_sold: "Tokens sold in this window.",
  attacker: "The wallet that traded on both sides of the victim.",
  victim: "The wallet that traded between the attacker's two trades.",
  profit_usd: "The attacker's sell dollars minus their buy dollars.",
  buy_tx: "The attacker's buy. Open it on Etherscan.",
  victim_tx: "The victim's swap. Open it on Etherscan.",
  sell_tx: "The attacker's sell. Open it on Etherscan.",
  received_amount: "Tokens this wallet received from the creator.",
  creator: "The creator address used for this row.",
  source: "The wallet that sent these holders their first tokens.",
  wallet_count: "How many wallets are in this cluster.",
  holdings: "Their end balances added up.",
  span_minutes: "Minutes from the first funding in the cluster to the last.",
  removed_percent: "Share of the liquidity added in this window that was removed.",
  removed_usd: "Dollar value. Blank when the pool units cannot be priced.",
  remover: "The wallet that removed the most in this pull.",
  original_adder: "The wallet that added liquidity first in this window.",
  same_wallet: "Whether the remover is the original adder.",
  amount_usd: "Transfer size at the average trade price in this window.",
  supply_percent: "Transfer size divided by the balances we could read.",
  tag: "Sent to an exchange, withdrew from an exchange, or just a large transfer.",
  buy_usd: "Dollars bought in this hour.",
  sell_usd: "Dollars sold in this hour.",
  net_usd: "Dollars bought minus dollars sold.",
  price_usd: "Average dollar price of the token in this hour.",
  label: "Bot or Human.",
  reason: "Which check matched, in plain language.",
  trade_count: "How many swaps this wallet made.",
  back_to_back_percent: "Share of steps from one trade to the next that land in the same block or the next one.",
  active_hours: "Hours from the first trade to the last.",
  wash_percent: "Flagged wash dollars divided by all swap dollars that day.",
  held_days: "How long this wallet has held the token.",
  ever_sold: "Whether this wallet has sold the token.",
  block_number: "The block the event was in.",
  block_timestamp: "When the event was mined.",
  protocol: "Uniswap v2, v3, or v4, or a fork if that option was on.",
  quote_token: "The other token in the pool.",
};

const ADDRESS_COLUMNS = new Set([
  "tx_hash",
  "wallet",
  "wallets",
  "pool_address",
  "sender",
  "recipient",
  "trader",
  "from_address",
  "to_address",
  "quote_token",
  "token",
  "attacker",
  "victim",
  "source",
  "creator",
  "remover",
  "original_adder",
  "buy_tx",
  "victim_tx",
  "sell_tx",
]);

const INTEGER_COLUMNS = new Set([
  "count",
  "swap_count",
  "round_trip_count",
  "ring_count",
  "n_buyers",
  "n_sellers",
  "n_wallets",
  "size",
  "rank",
  "block_number",
  "log_index",
  "fee",
  "start_block",
  "end_block",
  "held_days",
  "days_since_sell",
  "wallet_count",
  "trade_count",
  "first_block",
  "last_block",
]);
const TOKEN_COLUMNS = new Set([
  "amount_token",
  "sum_amount_token",
  "tokens_bought",
  "tokens_sold",
  "received_amount",
  "holdings",
]);
const PAGE_SIZE = 50;

function columnLabel(column: string): string {
  if (COLUMN_LABELS[column]) return COLUMN_LABELS[column];
  const words = column.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function columnTip(column: string): string {
  return COLUMN_TIPS[column] ?? `What ${columnLabel(column).toLowerCase()} means for this row.`;
}

function isUsd(column: string): boolean {
  return column === "usd_value" || column.endsWith("_usd") || column.endsWith("_usd_value");
}

function isPercent(column: string): boolean {
  return (
    column === "confidence" ||
    column === "ml_score" ||
    column === "heuristic_confidence" ||
    column.endsWith("_ratio") ||
    column.endsWith("_fraction") ||
    column.endsWith("_percent")
  );
}

function wraps(column: string): boolean {
  return column === "why" || column === "path" || column === "rules" || column === "wallets_used" || column === "reason";
}

type FlowStep = {
  kind?: string;
  side?: string;
  wallet?: string;
  from?: string;
  to?: string;
  amount?: number;
  usd?: number;
  tx?: string;
  time?: string;
  block?: number;
};

function FlowView({ raw, symbol }: { raw: unknown; symbol: string }) {
  let body: { why?: string; wallets?: string[]; steps?: FlowStep[] } = {};
  try {
    body = JSON.parse(String(raw ?? "{}")) as typeof body;
  } catch {
    body = {};
  }
  const steps = body.steps ?? [];
  const others = (body.wallets ?? []).slice(1);
  return (
    <div className="flow">
      {body.why && <p>{body.why}</p>}
      {others.length > 0 && (
        <p className="flow-wallets">
          Also used{" "}
          {others.map((wallet) => (
            <OneAddress key={wallet} text={wallet} tx={false} />
          ))}
        </p>
      )}
      <div className="flow-steps">
        {steps.map((step, index) => (
          <Fragment key={`${step.tx ?? ""}-${step.block ?? ""}-${index}`}>
            {index > 0 && <span className="flow-arrow">→</span>}
            <div className={`flow-step ${step.kind ?? ""}`}>
              <strong>{step.kind === "transfer" ? "Transfer" : step.side === "sell" ? "Sell" : "Buy"}</strong>
              {step.kind === "transfer" ? (
                <span>
                  <OneAddress text={step.from ?? ""} tx={false} /> to <OneAddress text={step.to ?? ""} tx={false} />
                </span>
              ) : (
                <OneAddress text={step.wallet ?? ""} tx={false} />
              )}
              <span>
                {Number(step.amount ?? 0).toLocaleString(undefined, { maximumFractionDigits: 4 })}
                {symbol ? ` ${symbol}` : ""}
                {step.kind !== "transfer" && step.usd != null
                  ? ` · ${Number(step.usd).toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 })}`
                  : ""}
              </span>
              {step.time && <span>{step.time.replace("T", " ").slice(0, 16)}</span>}
              {step.tx && <OneAddress text={step.tx} tx />}
            </div>
          </Fragment>
        ))}
      </div>
    </div>
  );
}

function TableCard({ output, noSwaps, symbol }: { output: OutputResult; noSwaps: boolean; symbol: string }) {
  const [sort, setSort] = useState<{ column: string; dir: "asc" | "desc" } | null>(null);
  const [page, setPage] = useState(0);
  const [open, setOpen] = useState<number | null>(null);
  const [flowOpen, setFlowOpen] = useState<number | null>(null);

  const rows = useMemo(() => {
    const copy = [...output.rows];
    if (!sort) return copy;
    const { column, dir } = sort;
    copy.sort((left, right) => compareCells(left[column], right[column]) * (dir === "asc" ? 1 : -1));
    return copy;
  }, [output.rows, sort]);

  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  const shown = rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  function toggleSort(column: string) {
    setPage(0);
    setSort((current) => {
      if (current?.column !== column) return { column, dir: "desc" };
      return { column, dir: current.dir === "desc" ? "asc" : "desc" };
    });
  }

  return (
    <article className="table-card">
      <div className="table-head">
        <h3>{output.title}</h3>
        {output.rows.length > 0 && (
          <button type="button" className="text" onClick={() => downloadCsv(output.title, output.columns, rows)}>
            Export CSV
          </button>
        )}
      </div>
      {output.rows.length === 0 ? (
        <p className="muted">
          {output.note
            ? output.note
            : noSwaps
              ? "No swaps were loaded for this window, so this is not a wash result."
              : "No rows."}
        </p>
      ) : (
        <>
          {output.note && <p className="hint">{output.note}</p>}
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  {output.columns.map((column) => (
                    <th key={column} className={wraps(column) ? "wrap" : undefined}>
                      <button type="button" className="sort" title={columnTip(column)} onClick={() => toggleSort(column)}>
                        {columnLabel(column)}
                        {sort?.column === column ? (sort.dir === "asc" ? " ↑" : " ↓") : ""}
                      </button>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {shown.map((row, index) => {
                  const rowIndex = page * PAGE_SIZE + index;
                  const long = output.columns.some((column) => wraps(column) && String(row[column] ?? "").length > 80);
                  return (
                    <Fragment key={rowIndex}>
                      <tr>
                        {output.columns.map((column) => (
                          <td key={column} className={wraps(column) ? "wrap" : undefined}>
                            {column === "flow" ? (
                              <button
                                type="button"
                                className="text"
                                onClick={() => setFlowOpen(flowOpen === rowIndex ? null : rowIndex)}
                              >
                                {flowOpen === rowIndex ? "Hide flow" : "View flow"}
                              </button>
                            ) : (
                              <Cell value={row[column]} column={column} symbol={symbol} expanded={open === rowIndex} />
                            )}
                            {long && wraps(column) && column !== "flow" && (
                              <button
                                type="button"
                                className="text"
                                onClick={() => setOpen(open === rowIndex ? null : rowIndex)}
                              >
                                {open === rowIndex ? "Hide" : "More"}
                              </button>
                            )}
                          </td>
                        ))}
                      </tr>
                      {flowOpen === rowIndex && row.flow != null && (
                        <tr>
                          <td colSpan={output.columns.length}>
                            <FlowView raw={row.flow} symbol={symbol} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
          {pages > 1 && (
            <p className="pager">
              <button type="button" className="text" disabled={page === 0} onClick={() => setPage((value) => value - 1)}>
                Previous
              </button>
              <span>
                {page + 1} of {pages} · {rows.length.toLocaleString()} rows
              </span>
              <button
                type="button"
                className="text"
                disabled={page + 1 >= pages}
                onClick={() => setPage((value) => value + 1)}
              >
                Next
              </button>
            </p>
          )}
        </>
      )}
      {output.truncated && <p className="hint">Showing the first {output.rows.length} rows.</p>}
    </article>
  );
}

function ChartCard({ output }: { output: OutputResult }) {
  const rows = output.rows;
  const pressure = rows.some((row) => row.buy_usd !== undefined || row.sell_usd !== undefined);
  const washShare = !pressure && rows.some((row) => row.wash_percent !== undefined && row.wash_percent !== null);
  const totals = rows.map((row) => numberOf(row.sum_usd_value));
  const wash = rows.map((row) => numberOf(row.sum_wash_usd));
  const hasWash = rows.some((row) => row.sum_wash_usd !== undefined && row.sum_wash_usd !== null);
  const max = pressure
    ? Math.max(1, ...rows.map((row) => Math.max(numberOf(row.buy_usd), numberOf(row.sell_usd))))
    : Math.max(1, ...totals, ...wash);
  return (
    <article className="chart-card">
      <h3>{output.title}</h3>
      {output.note && <p className="hint">{output.note}</p>}
      {rows.length === 0 ? (
        output.note ? null : <p className="muted">No days to plot.</p>
      ) : (
        <div className="bars" role="img" aria-label={output.title}>
          {rows.map((row, index) => {
            const label = String(row.day ?? row.wallet ?? row.pool_address ?? index);
            if (pressure) {
              const buy = numberOf(row.buy_usd);
              const sell = numberOf(row.sell_usd);
              const price = row.price_usd == null ? "" : ` · ${numberOf(row.price_usd).toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 4 })}`;
              return (
                <div key={label} className="bar-col" title={`${label}: bought ${buy}, sold ${sell}${price}`}>
                  <div className="bar pair">
                    <span className="buy" style={{ height: `${(buy / max) * 100}%` }} />
                    <span className="sell" style={{ height: `${(sell / max) * 100}%` }} />
                  </div>
                  <span className="bar-label">{label.slice(5, 16)}</span>
                  {row.price_usd != null && (
                    <span className="bar-price">
                      {numberOf(row.price_usd).toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 })}
                    </span>
                  )}
                </div>
              );
            }
            if (washShare) {
              const share = numberOf(row.wash_percent);
              return (
                <div key={label} className="bar-col" title={`${label}: ${(share * 100).toFixed(1)}% wash`}>
                  <div className="bar">
                    <span className="wash" style={{ height: `${share * 100}%` }} />
                  </div>
                  <span className="bar-label">{label.slice(5)}</span>
                </div>
              );
            }
            const total = totals[index] ?? 0;
            const washPart = Math.min(wash[index] ?? 0, total);
            const rest = Math.max(0, total - washPart);
            return (
              <div key={label} className="bar-col" title={`${label}: ${total}`}>
                <div className="bar">
                  <span className="rest" style={{ height: `${(rest / max) * 100}%` }} />
                  {hasWash && <span className="wash" style={{ height: `${(washPart / max) * 100}%` }} />}
                </div>
                <span className="bar-label">{label.slice(5)}</span>
              </div>
            );
          })}
        </div>
      )}
      <p className="legend">
        {pressure ? (
          <>
            <i className="swatch buy" /> bought
            <i className="swatch sell" /> sold
          </>
        ) : washShare ? (
          <>
            <i className="swatch wash" /> wash share of volume
          </>
        ) : (
          <>
            <i className="swatch rest" /> volume
            {hasWash && (
              <>
                <i className="swatch wash" /> flagged wash
              </>
            )}
          </>
        )}
      </p>
    </article>
  );
}

function Cell({
  value,
  column,
  symbol,
  expanded,
}: {
  value: unknown;
  column: string;
  symbol: string;
  expanded: boolean;
}) {
  if (ADDRESS_COLUMNS.has(column)) return <AddressCell value={value} tx={column === "tx_hash" || column.endsWith("_tx")} />;
  const text = formatCell(value, column, symbol);
  if (!wraps(column)) return <>{text}</>;
  return <span className={expanded ? "wrap-text open" : "wrap-text"}>{text}</span>;
}

function AddressCell({ value, tx }: { value: unknown; tx: boolean }) {
  const text = value === null || value === undefined ? "" : String(value);
  const parts = text.split(/[,\s]+/).filter((part) => part.startsWith("0x") && part.length >= 12);
  if (parts.length === 0) return <>{text}</>;
  return (
    <span className="addr-list">
      {parts.map((part) => (
        <OneAddress key={part} text={part} tx={tx && parts.length === 1} />
      ))}
    </span>
  );
}

function OneAddress({ text, tx }: { text: string; tx: boolean }) {
  const short = `${text.slice(0, 6)}…${text.slice(-5)}`;
  const href = tx ? `https://etherscan.io/tx/${text}` : `https://etherscan.io/address/${text}`;
  return (
    <span className="addr">
      <a href={href} target="_blank" rel="noreferrer">
        {short}
      </a>
      <button type="button" className="text" onClick={() => void navigator.clipboard.writeText(text)}>
        Copy
      </button>
    </span>
  );
}

function formatCell(value: unknown, column: string, symbol: string): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "boolean") return value ? "yes" : "no";
  const numeric = typeof value === "number" ? value : Number(value);
  if (isUsd(column) && Number.isFinite(numeric)) {
    return numeric.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
  }
  if (isPercent(column) && Number.isFinite(numeric)) {
    const pct = numeric * 100;
    const digits = Number.isInteger(pct) ? 0 : 1;
    return `${pct.toLocaleString(undefined, { maximumFractionDigits: digits })}%`;
  }
  if (TOKEN_COLUMNS.has(column) || column === "amount_quote") {
    if (!Number.isFinite(numeric)) return String(value);
    const formatted = numeric.toLocaleString(undefined, { maximumFractionDigits: 4 });
    return TOKEN_COLUMNS.has(column) && symbol ? `${formatted} ${symbol}` : formatted;
  }
  if (INTEGER_COLUMNS.has(column) && Number.isFinite(numeric)) return Math.round(numeric).toLocaleString();
  if (typeof value === "number") return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  return String(value);
}

function compareCells(left: unknown, right: unknown): number {
  const ln = typeof left === "number" ? left : Number(left);
  const rn = typeof right === "number" ? right : Number(right);
  if (Number.isFinite(ln) && Number.isFinite(rn)) return ln - rn;
  return String(left ?? "").localeCompare(String(right ?? ""));
}

function downloadCsv(title: string, columns: string[], rows: Array<Record<string, unknown>>) {
  const lines = [columns.join(",")];
  for (const row of rows) {
    lines.push(columns.map((column) => csvCell(row[column])).join(","));
  }
  const blob = new Blob([lines.join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${title.replaceAll(/\s+/g, "-").toLowerCase() || "results"}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}

function csvCell(value: unknown): string {
  const text = value === null || value === undefined ? "" : String(value);
  if (/[",\n]/.test(text)) return `"${text.replaceAll('"', '""')}"`;
  return text;
}

function formatNumber(value: unknown, format: string): string {
  if (value === null || value === undefined || value === "") return "—";
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) return "—";
  if (format === "percent") return `${numeric.toLocaleString(undefined, { maximumFractionDigits: 2 })}%`;
  if (format === "integer") return Math.round(numeric).toLocaleString();
  return numeric.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

function numberOf(value: unknown): number {
  const numeric = typeof value === "number" ? value : Number(value ?? 0);
  return Number.isFinite(numeric) ? numeric : 0;
}

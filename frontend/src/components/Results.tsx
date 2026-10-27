import { Fragment, useMemo, useState, type PointerEvent as ReactPointerEvent } from "react";
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
  noSwaps,
  symbol,
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
  noSwaps: boolean;
  symbol: string;
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
        <button type="button" className={tab === "result" ? "on" : ""} onClick={() => openTab("result")}>
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
        <ResultBody run={run} runError={runError} noSwaps={noSwaps} symbol={symbol} />
      )}
    </section>
  );
}

function ResultBody({
  run,
  runError,
  noSwaps,
  symbol,
}: {
  run: RunResult | null;
  runError: string | null;
  noSwaps: boolean;
  symbol: string;
}) {
  if (runError) return <p className="banner error">{runError}</p>;
  if (!run) return <p className="muted pad">Nothing has been run. Loading a token does not calculate anything.</p>;
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
      {run.outputs.map((output, index) => (
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
};

const ADDRESS_COLUMNS = new Set([
  "tx_hash",
  "wallet",
  "pool_address",
  "sender",
  "recipient",
  "trader",
  "quote_token",
  "token",
]);

const USD_COLUMNS = new Set(["usd_value", "sum_usd_value", "sum_wash_usd", "ml_weighted_usd"]);
const TOKEN_COLUMNS = new Set(["amount_token", "sum_amount_token"]);
const PAGE_SIZE = 50;

function columnLabel(column: string): string {
  return COLUMN_LABELS[column] ?? column.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function wraps(column: string): boolean {
  return column === "why" || column === "path" || column === "rules";
}

function TableCard({ output, noSwaps, symbol }: { output: OutputResult; noSwaps: boolean; symbol: string }) {
  const [sort, setSort] = useState<{ column: string; dir: "asc" | "desc" } | null>(null);
  const [page, setPage] = useState(0);
  const [open, setOpen] = useState<number | null>(null);

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
          {noSwaps ? "No swaps were loaded for this window, so this is not a wash result." : "No rows."}
        </p>
      ) : (
        <>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  {output.columns.map((column) => (
                    <th key={column} className={wraps(column) ? "wrap" : undefined}>
                      <button type="button" className="sort" onClick={() => toggleSort(column)}>
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
                            <Cell value={row[column]} column={column} symbol={symbol} expanded={open === rowIndex} />
                            {long && wraps(column) && (
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
  const totals = rows.map((row) => numberOf(row.sum_usd_value));
  const wash = rows.map((row) => numberOf(row.sum_wash_usd));
  const hasWash = rows.some((row) => row.sum_wash_usd !== undefined && row.sum_wash_usd !== null);
  const max = Math.max(1, ...totals, ...wash);
  return (
    <article className="chart-card">
      <h3>{output.title}</h3>
      {rows.length === 0 ? (
        <p className="muted">No days to plot.</p>
      ) : (
        <div className="bars" role="img" aria-label={output.title}>
          {rows.map((row, index) => {
            const total = totals[index] ?? 0;
            const washPart = Math.min(wash[index] ?? 0, total);
            const rest = Math.max(0, total - washPart);
            const label = String(row.day ?? row.wallet ?? row.pool_address ?? index);
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
        <i className="swatch rest" /> volume
        {hasWash && (
          <>
            <i className="swatch wash" /> flagged wash
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
  if (ADDRESS_COLUMNS.has(column)) return <AddressCell value={value} tx={column === "tx_hash"} />;
  const text = formatCell(value, column, symbol);
  if (!wraps(column)) return <>{text}</>;
  return <span className={expanded ? "wrap-text open" : "wrap-text"}>{text}</span>;
}

function AddressCell({ value, tx }: { value: unknown; tx: boolean }) {
  const text = value === null || value === undefined ? "" : String(value);
  if (!text.startsWith("0x") || text.length < 12) return <>{text}</>;
  const short = `${text.slice(0, 6)}…${text.slice(-4)}`;
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
  if (USD_COLUMNS.has(column)) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return String(value);
    return numeric.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 });
  }
  if (TOKEN_COLUMNS.has(column) || column === "amount_quote") {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return String(value);
    const formatted = numeric.toLocaleString(undefined, { maximumFractionDigits: 4 });
    return TOKEN_COLUMNS.has(column) && symbol ? `${formatted} ${symbol}` : formatted;
  }
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

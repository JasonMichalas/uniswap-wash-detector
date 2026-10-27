import type { PointerEvent as ReactPointerEvent } from "react";
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
}) {
  function openTab(next: "query" | "sql" | "result") {
    onCollapsed(false);
    onTab(next);
  }

  function startResize(event: ReactPointerEvent<HTMLDivElement>) {
    event.preventDefault();
    const startY = event.clientY;
    const start = height;
    function move(ev: PointerEvent) {
      const max = Math.round(window.innerHeight * 0.5);
      onHeight(Math.min(max, Math.max(120, start + (startY - ev.clientY))));
    }
    function up() {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  return (
    <section className={collapsed ? "bottom collapsed" : "bottom"} style={collapsed ? undefined : { height }}>
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
      {!collapsed && tab === "result" && <ResultBody run={run} runError={runError} />}
    </section>
  );
}

function ResultBody({ run, runError }: { run: RunResult | null; runError: string | null }) {
  if (runError) return <p className="banner error">{runError}</p>;
  if (!run) return <p className="muted pad">Nothing has been run. Loading a token does not calculate anything.</p>;
  return (
    <div className="result-stack">
      {run.warnings.map((warning) => (
        <p key={warning} className="banner warn">
          {warning}
        </p>
      ))}
      {run.outputs.map((output, index) => (
        <OutputView key={`${output.title}-${index}`} output={output} />
      ))}
    </div>
  );
}

function OutputView({ output }: { output: OutputResult }) {
  if (output.kind === "number") return <NumberCard output={output} />;
  if (output.kind === "chart") return <ChartCard output={output} />;
  return <TableCard output={output} />;
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

function TableCard({ output }: { output: OutputResult }) {
  return (
    <article className="table-card">
      <h3>{output.title}</h3>
      {output.rows.length === 0 ? (
        <p className="muted">No rows.</p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                {output.columns.map((column) => (
                  <th key={column}>{column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {output.rows.map((row, index) => (
                <tr key={index}>
                  {output.columns.map((column) => (
                    <td key={column}>{cell(row[column])}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
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

function formatNumber(value: unknown, format: string): string {
  if (value === null || value === undefined || value === "") return "—";
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) return "—";
  if (format === "percent") return `${numeric.toLocaleString(undefined, { maximumFractionDigits: 2 })}%`;
  if (format === "integer") return Math.round(numeric).toLocaleString();
  return numeric.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

function cell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  return String(value);
}

function numberOf(value: unknown): number {
  const numeric = typeof value === "number" ? value : Number(value ?? 0);
  return Number.isFinite(numeric) ? numeric : 0;
}

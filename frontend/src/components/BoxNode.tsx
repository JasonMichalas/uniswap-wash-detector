import { Handle, Position, type NodeProps } from "@xyflow/react";
import { nodeDef, type BoxNode } from "../flow";
import { useCatalog } from "./catalog";

export function BoxNodeView({ data }: NodeProps<BoxNode>) {
  const catalog = useCatalog();
  const def = nodeDef(catalog, data.nodeType);
  const inputs = def?.inputs ?? [];
  const outputs = def?.outputs ?? [];
  return (
    <article className={`box cat-${def?.category ?? "other"}`}>
      <header>
        <span className="cat">{friendlyCategory(def?.category)}</span>
        <strong>{data.label}</strong>
      </header>
      <p className="summary">{summary(data.nodeType, data.params)}</p>
      {inputs.map((socket, index) => (
        <Handle
          key={socket.id}
          type="target"
          position={Position.Left}
          id={socket.id}
          style={{ top: socketTop(index, inputs.length) }}
          title={socket.label}
        />
      ))}
      {outputs.map((socket, index) => (
        <Handle
          key={socket.id}
          type="source"
          position={Position.Right}
          id={socket.id}
          style={{ top: socketTop(index, outputs.length) }}
          title={socket.label}
        />
      ))}
      <div className="sockets">
        <ul>
          {inputs.map((socket) => (
            <li key={socket.id}>{socket.label}</li>
          ))}
        </ul>
        <ul className="right">
          {outputs.map((socket) => (
            <li key={socket.id}>{socket.label}</li>
          ))}
        </ul>
      </div>
    </article>
  );
}

function friendlyCategory(category: string | undefined): string {
  if (category === "source") return "Data";
  if (category === "filter") return "Filter";
  if (category === "detector") return "Detect";
  if (category === "aggregate" || category === "arrange") return "Combine";
  if (category === "output") return "Show";
  return "Box";
}

function socketTop(index: number, count: number): string {
  if (count <= 1) return "62%";
  return `${48 + ((index + 1) / (count + 1)) * 48}%`;
}

export function summary(nodeType: string, params: Record<string, unknown>): string {
  if (nodeType === "source") return String(params.source ?? "pick a table");
  if (nodeType === "condition") {
    if (params.mode === "sold_in") {
      const days = params.days ?? "?";
      return params.negate ? `not sold in ${days}d` : `sold in ${days}d`;
    }
    const field = params.field || "field";
    const op = params.op || "=";
    return `${field} ${op} ${formatValue(params.value)}`;
  }
  if (nodeType === "time_window") return `last ${params.days ?? "?"}d of the load`;
  if (nodeType === "detect_cycles") {
    const tolerance = params.tolerance ?? 0.05;
    const windowBlocks = params.window_blocks ?? 200;
    const group = params.max_group_size ?? 16;
    return `tol ${tolerance}, ${windowBlocks} blocks, ≤${group} wallets`;
  }
  if (nodeType === "ml_score") return "XGBoost probability";
  if (nodeType === "anomaly_score") return "Isolation Forest";
  if (nodeType === "explain") return "top SHAP reasons";
  if (nodeType === "group_by") return `by ${params.by ?? "?"}`;
  if (nodeType === "aggregate") {
    return params.fn === "count" ? "count" : `${params.fn ?? "sum"} ${params.field || ""}`.trim();
  }
  if (nodeType === "ratio") {
    const field = String(params.field || "field");
    const over = String(params.over || "");
    return over ? `100 × ${field} / ${over}` : `100 × ${field}`;
  }
  if (nodeType === "sort") return `${params.field || "field"} ${params.direction ?? "desc"}`;
  if (nodeType === "limit") return `first ${params.n ?? "?"}`;
  if (String(nodeType).startsWith("output")) return String(params.title || "untitled");
  return "";
}

function formatValue(value: unknown): string {
  if (value === true) return "true";
  if (value === false) return "false";
  if (value === "" || value === undefined || value === null) return "?";
  return String(value);
}

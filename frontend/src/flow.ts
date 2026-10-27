import type { Connection, Edge, Node } from "@xyflow/react";
import type { ApiGraph, Catalog, FieldDef } from "./types";

export type BoxData = {
  nodeType: string;
  params: Record<string, unknown>;
  label: string;
};

export type BoxNode = Node<BoxData, "box">;

export type StreamInfo = { type: string; fields: FieldDef[] };

const GROUP_KEYS: Record<string, string[]> = {
  swaps: ["day", "wallet", "pool"],
  swaps_flagged: ["day", "wallet", "pool"],
  transfers: ["day"],
  wash_wallets: ["wallet"],
  pools: ["pool"],
};

export function nodeDef(catalog: Catalog, nodeType: string) {
  return catalog.nodes.find((item) => item.type === nodeType);
}

function paramsForRun(nodeType: string, params: Record<string, unknown>): Record<string, unknown> {
  if (nodeType !== "detect_cycles") return params;
  // The inspector shows these when a box was saved with nothing filled in.
  // Run and the query preview have to use the same numbers.
  const next = { ...params };
  if (next.tolerance == null || next.tolerance === "") next.tolerance = 0.05;
  if (next.window_blocks == null || next.window_blocks === "") next.window_blocks = 7200;
  if (next.max_group_size == null || next.max_group_size === "") next.max_group_size = 24;
  if (next.rules == null || next.rules === "") next.rules = "R1,R2,R3,R4,R5,R6";
  return next;
}

export function toApiGraph(nodes: BoxNode[], edges: Edge[], name?: string): ApiGraph {
  return {
    name,
    nodes: nodes.map((node) => ({
      id: node.id,
      type: node.data.nodeType,
      position: node.position,
      data: paramsForRun(node.data.nodeType, node.data.params),
    })),
    edges: edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      sourceHandle: edge.sourceHandle,
      targetHandle: edge.targetHandle,
    })),
  };
}

export function fromApiGraph(graph: ApiGraph, catalog: Catalog): { nodes: BoxNode[]; edges: Edge[] } {
  const nodes: BoxNode[] = graph.nodes.map((node) => {
    const semantic = node.type === "box" ? String(node.data.nodeType ?? "") : node.type;
    const params =
      node.type === "box" && node.data.params && typeof node.data.params === "object"
        ? (node.data.params as Record<string, unknown>)
        : { ...node.data };
    delete params.nodeType;
    const def = nodeDef(catalog, semantic);
    const label = sourceLabel(catalog, semantic, params) || def?.label || semantic;
    return {
      id: node.id,
      type: "box",
      position: node.position ?? { x: 0, y: 0 },
      data: { nodeType: semantic, params, label },
    };
  });
  const edges: Edge[] = (graph.edges ?? []).map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    sourceHandle: edge.sourceHandle ?? undefined,
    targetHandle: edge.targetHandle ?? undefined,
  }));
  return { nodes, edges };
}

export function sourceLabel(catalog: Catalog, nodeType: string, params: Record<string, unknown>): string {
  if (nodeType !== "source") return "";
  const match = catalog.palette.find(
    (item) => item.node_type === "source" && item.data.source === params.source,
  );
  return match?.label ?? "";
}

export function canConnectTypes(src: string, dst: string, catalog: Catalog): boolean {
  if (dst === "rows") return catalog.connect.rows.includes(src);
  if (dst === "timed") return catalog.connect.timed.includes(src);
  if (dst === "walletish") return catalog.connect.walletish.includes(src);
  if (dst === "scoreable") return catalog.connect.scoreable.includes(src);
  if (dst === "swaps") return src === "swaps";
  return src === dst;
}

export function inferOutputs(
  nodes: BoxNode[],
  edges: Edge[],
  catalog: Catalog,
): Map<string, Record<string, StreamInfo>> {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const order = topo(nodes, edges);
  const outputs = new Map<string, Record<string, StreamInfo>>();

  for (const id of order) {
    const node = byId.get(id);
    if (!node) continue;
    const produced = produce(node, edges, outputs, catalog);
    outputs.set(id, produced);
  }
  return outputs;
}

function produce(
  node: BoxNode,
  edges: Edge[],
  outputs: Map<string, Record<string, StreamInfo>>,
  catalog: Catalog,
): Record<string, StreamInfo> {
  const ntype = node.data.nodeType;
  const params = node.data.params;
  if (ntype === "source") {
    const source = String(params.source ?? "");
    const typeName = catalog.source_types[source];
    if (!typeName) return {};
    return { out: { type: typeName, fields: catalog.types[typeName] ?? [] } };
  }
  if (ntype === "detect_cycles") {
    return {
      out_swaps: stream(catalog, "swaps_flagged"),
      out_wallets: stream(catalog, "wash_wallets"),
      out_cycles: stream(catalog, "cycles"),
    };
  }
  if (ntype === "group_by") {
    const input = singleInput(node, edges, outputs, catalog);
    const key = String(params.by ?? "");
    const fields: FieldDef[] = [{ name: "count", kind: "number" }];
    if (key === "day") {
      fields.unshift({ name: "day", kind: "string" });
      fields.push({ name: "sum_usd_value", kind: "number" });
      if (input?.fields.some((field) => field.name === "is_wash")) {
        fields.push({ name: "sum_wash_usd", kind: "number" });
      }
    } else if (key === "wallet") {
      fields.unshift({ name: "wallet", kind: "string" });
      fields.push({ name: "sum_usd_value", kind: "number" });
    } else if (key === "pool") {
      fields.unshift({ name: "pool_address", kind: "string" });
      fields.push({ name: "sum_usd_value", kind: "number" });
    }
    return { out: { type: "grouped", fields } };
  }
  if (ntype === "aggregate" || ntype === "ratio") {
    return { out: stream(catalog, "scalar") };
  }
  if (ntype === "explain") {
    return { out: stream(catalog, "explanations") };
  }
  if (
    ntype === "condition" ||
    ntype === "time_window" ||
    ntype === "sort" ||
    ntype === "limit" ||
    ntype === "ml_score" ||
    ntype === "anomaly_score"
  ) {
    const input = singleInput(node, edges, outputs, catalog);
    if (!input) return {};
    const fields = input.fields.map((field) => ({ ...field }));
    if (ntype === "ml_score" && !fields.some((field) => field.name === "ml_score")) {
      fields.push({ name: "ml_score", kind: "number" });
      if (fields.some((field) => field.name === "usd_value")) {
        fields.push({ name: "ml_weighted_usd", kind: "number" });
      }
    }
    if (ntype === "anomaly_score" && !fields.some((field) => field.name === "anomaly_score")) {
      fields.push({ name: "anomaly_score", kind: "number" });
    }
    return { out: { type: input.type, fields } };
  }
  return {};
}

function stream(catalog: Catalog, typeName: string): StreamInfo {
  return { type: typeName, fields: catalog.types[typeName] ?? [] };
}

export function singleInput(
  node: BoxNode,
  edges: Edge[],
  outputs: Map<string, Record<string, StreamInfo>>,
  catalog: Catalog,
): StreamInfo | null {
  const def = nodeDef(catalog, node.data.nodeType);
  if (!def || def.inputs.length !== 1) return null;
  const handle = def.inputs[0].id;
  const wire = edges.find((edge) => edge.target === node.id && (edge.targetHandle || "in") === handle);
  if (!wire) return null;
  return outputs.get(wire.source)?.[wire.sourceHandle || "out"] ?? null;
}

export function inputOnHandle(
  node: BoxNode,
  handle: string,
  edges: Edge[],
  outputs: Map<string, Record<string, StreamInfo>>,
): StreamInfo | null {
  const wire = edges.find((edge) => edge.target === node.id && (edge.targetHandle || "in") === handle);
  if (!wire) return null;
  return outputs.get(wire.source)?.[wire.sourceHandle || "out"] ?? null;
}

export function groupKeysFor(typeName: string | undefined): string[] {
  if (!typeName) return ["day", "wallet", "pool"];
  return GROUP_KEYS[typeName] ?? [];
}

export function wouldCycle(source: string, target: string, edges: Edge[]): boolean {
  const stack = [target];
  const seen = new Set<string>();
  while (stack.length) {
    const id = stack.pop();
    if (!id || seen.has(id)) continue;
    if (id === source) return true;
    seen.add(id);
    for (const edge of edges) {
      if (edge.source === id) stack.push(edge.target);
    }
  }
  return false;
}

export function connectionAllowed(
  connection: Connection | Edge,
  nodes: BoxNode[],
  edges: Edge[],
  catalog: Catalog,
): boolean {
  const source = connection.source;
  const target = connection.target;
  if (!source || !target || source === target) return false;
  const sourceHandle = connection.sourceHandle || "out";
  const targetHandle = connection.targetHandle || "in";
  const taken = edges.some(
    (edge) =>
      edge.target === target &&
      (edge.targetHandle || "in") === targetHandle &&
      edge.source !== source,
  );
  if (taken) return false;
  if (wouldCycle(source, target, edges)) return false;
  const outputs = inferOutputs(nodes, edges, catalog);
  const srcType = outputs.get(source)?.[sourceHandle]?.type;
  const targetNode = nodes.find((node) => node.id === target);
  if (!srcType || !targetNode) return false;
  const def = nodeDef(catalog, targetNode.data.nodeType);
  const input = def?.inputs.find((socket) => socket.id === targetHandle);
  if (!input) return false;
  return canConnectTypes(srcType, input.type, catalog);
}

function topo(nodes: BoxNode[], edges: Edge[]): string[] {
  const indeg = new Map(nodes.map((node) => [node.id, 0]));
  const next = new Map(nodes.map((node) => [node.id, [] as string[]]));
  for (const edge of edges) {
    if (!indeg.has(edge.source) || !indeg.has(edge.target)) continue;
    next.get(edge.source)?.push(edge.target);
    indeg.set(edge.target, (indeg.get(edge.target) ?? 0) + 1);
  }
  const queue = [...indeg.entries()].filter(([, degree]) => degree === 0).map(([id]) => id);
  const order: string[] = [];
  while (queue.length) {
    const id = queue.shift();
    if (!id) break;
    order.push(id);
    for (const child of next.get(id) ?? []) {
      const degree = (indeg.get(child) ?? 1) - 1;
      indeg.set(child, degree);
      if (degree === 0) queue.push(child);
    }
  }
  if (order.length !== nodes.length) {
    for (const node of nodes) {
      if (!order.includes(node.id)) order.push(node.id);
    }
  }
  return order;
}

export function logicKey(nodes: BoxNode[], edges: Edge[]): string {
  return JSON.stringify({
    nodes: nodes.map((node) => ({
      id: node.id,
      type: node.data.nodeType,
      data: node.data.params,
    })),
    edges: edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      sourceHandle: edge.sourceHandle,
      targetHandle: edge.targetHandle,
    })),
  });
}

export function isAddress(value: string): boolean {
  return /^0x[a-fA-F0-9]{40}$/.test(value.trim());
}

export function rangeProblem(start: string, end: string): string | null {
  if (!start || !end) return "Choose a start date and an end date.";
  const startMs = Date.parse(`${start}T00:00:00Z`);
  const endMs = Date.parse(`${end}T00:00:00Z`);
  if (Number.isNaN(startMs) || Number.isNaN(endMs)) return "Those dates are not valid.";
  if (endMs < startMs) return "The end date is before the start date.";
  const days = Math.round((endMs - startMs) / 86_400_000) + 1;
  if (days > 90) return "A custom range can be at most 90 days.";
  return null;
}

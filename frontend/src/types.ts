export type FieldKind = "number" | "string" | "bool";

export type FieldDef = { name: string; kind: FieldKind };

export type SocketDef = {
  id: string;
  label: string;
  type: string;
  typeFrom?: string;
};

export type ParamDef = {
  name: string;
  label: string;
  kind: "enum" | "field" | "value" | "number" | "bool" | "text";
  options?: string[];
};

export type NodeDef = {
  type: string;
  label: string;
  category: string;
  description: string;
  inputs: SocketDef[];
  outputs: SocketDef[];
  params: ParamDef[];
};

export type PaletteItem = {
  node_type: string;
  label: string;
  category: string;
  blurb: string;
  data: Record<string, unknown>;
};

export type Catalog = {
  palette: PaletteItem[];
  nodes: NodeDef[];
  types: Record<string, FieldDef[]>;
  row_types: string[];
  source_types: Record<string, string>;
  connect: {
    rows: string[];
    timed: string[];
    walletish: string[];
    swaps_exact: string[];
  };
};

export type GraphError = {
  message: string;
  node_id: string | null;
  edge_id: string | null;
  code: string;
};

export type ApiNode = {
  id: string;
  type: string;
  position?: { x: number; y: number };
  data: Record<string, unknown>;
};

export type ApiEdge = {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string | null;
  targetHandle?: string | null;
};

export type ApiGraph = {
  name?: string;
  description?: string;
  nodes: ApiNode[];
  edges: ApiEdge[];
};

export type LoadResult = {
  token: string;
  symbol: string;
  decimals: number;
  start_block: number;
  end_block: number;
  start_time: string;
  end_time: string;
  pools: number;
  v2_pools: number;
  v3_pools: number;
  swaps: number;
  transfers: number;
  priced_swaps: number;
  balance_wallets: number;
  balances_truncated: boolean;
  blocks_fetched: number;
  blocks_cached: number;
  seconds: number;
  eth_usd: number | null;
  used_subgraph: boolean;
  notes: string[];
  analysis_ran: boolean;
};

export type OutputResult = {
  kind: "number" | "table" | "chart" | string;
  title: string;
  format: string;
  columns: string[];
  rows: Record<string, unknown>[];
  truncated: boolean;
};

export type RunResult = {
  query: string;
  sql: string;
  outputs: OutputResult[];
  warnings: string[];
};

export type TemplateSummary = {
  id: string;
  name: string;
  description: string;
};

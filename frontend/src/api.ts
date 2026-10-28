import type {
  ApiGraph,
  Catalog,
  GraphError,
  LoadResult,
  RunResult,
  TemplateSummary,
  TokenSuggestions,
} from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  const text = await response.text();
  const body = text ? (JSON.parse(text) as unknown) : null;
  if (!response.ok) {
    throw new ApiError(response.status, messageOf(body));
  }
  return body as T;
}

function messageOf(body: unknown): string {
  if (!body || typeof body !== "object") return "The request failed.";
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "errors" in detail) {
    const errors = (detail as { errors: GraphError[] }).errors;
    return errors.map((item) => item.message).join(" ");
  }
  if (typeof (body as { message?: unknown }).message === "string") {
    return (body as { message: string }).message;
  }
  return "The request failed.";
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function fetchCatalog(): Promise<Catalog> {
  return request("/api/catalog");
}

export function fetchTemplates(): Promise<TemplateSummary[]> {
  return request<{ templates: TemplateSummary[] }>("/api/templates").then((body) => body.templates);
}

export function fetchTemplate(id: string): Promise<ApiGraph> {
  return request(`/api/templates/${encodeURIComponent(id)}`);
}

export function compileGraph(
  graph: ApiGraph,
): Promise<{ ok: boolean; errors: GraphError[]; query: string; sql: string }> {
  return request("/api/graph/compile", { method: "POST", body: JSON.stringify(graph) });
}

export function runGraph(payload: {
  token: string;
  start_block: number;
  end_block: number;
  start_time: string;
  end_time: string;
  graph: ApiGraph;
}): Promise<RunResult> {
  return request("/api/graph/run", { method: "POST", body: JSON.stringify(payload) });
}

export function startLoad(body: {
  token: string;
  days?: number;
  start?: string;
  end?: string;
  include_forks?: boolean;
}): Promise<{ job_id: string }> {
  return request("/api/token/load", { method: "POST", body: JSON.stringify(body) });
}

export function cancelJob(id: string): Promise<{ ok: boolean }> {
  return request(`/api/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST", body: "{}" });
}

export function fetchSuggestions(query?: string): Promise<TokenSuggestions> {
  const path = query ? `/api/tokens/suggest?q=${encodeURIComponent(query)}` : "/api/tokens/suggest";
  return request(path);
}

export function fetchJob(id: string): Promise<{
  id: string;
  status: "running" | "done" | "error" | "cancelled";
  message: string;
  result: LoadResult | null;
  error: string | null;
  error_detail?: string | null;
}> {
  return request(`/api/jobs/${encodeURIComponent(id)}`);
}

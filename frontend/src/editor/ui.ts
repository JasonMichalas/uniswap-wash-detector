import type { LoadResult } from "../types";

export const PRESETS = [7, 30, 60, 90];

export const GROUPS: { id: string; title: string; categories: string[] }[] = [
  { id: "data", title: "Data", categories: ["source"] },
  { id: "filter", title: "Filter", categories: ["filter"] },
  { id: "detect", title: "Detect", categories: ["detector"] },
  { id: "combine", title: "Combine", categories: ["aggregate", "arrange"] },
  { id: "show", title: "Show", categories: ["output"] },
];

export function stepHint(
  step: 1 | 2 | 3,
  loading: boolean,
  preset: number | null,
  boxes: number,
  problems: number,
): string {
  if (loading) return "Loading this token. Nothing is calculated yet.";
  if (step === 1) return "Search by name, or pick a token from the list. You do not need a contract address.";
  if (step === 2) {
    return preset === 7
      ? "Seven days is already selected. Press Load token. Nothing is calculated yet."
      : "Press Load token. Nothing is calculated yet.";
  }
  if (boxes === 0) return "Drag a Data box onto the canvas, connect the boxes, then press Run.";
  if (problems > 0) return "Fix the highlighted box, then press Run.";
  return "Connect the boxes, then press Run.";
}

export function actionStatus(loaded: LoadResult | null, boxes: number, problems: number, running: boolean): string {
  if (running) return "Running your question.";
  if (!loaded) return "Run stays off until a token is loaded.";
  if (boxes === 0) return "Add a box, then press Run.";
  if (problems > 0) return "Run stays off while a box has a problem.";
  return "Ready to run.";
}

export function addressProblem(text: string): string | null {
  const trimmed = text.trim();
  if (!trimmed.toLowerCase().startsWith("0x")) return null;
  const body = trimmed.slice(2);
  if (body.length === 0) return null;
  const hex = /^[0-9a-fA-F]+$/.test(body);
  if (!hex || body.length > 40) {
    return "That address needs 0x and 40 hex characters. Or pick a token from the list.";
  }
  return null;
}

export function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

export function shortAddress(value: string): string {
  return `${value.slice(0, 6)}…${value.slice(-4)}`;
}

export function slug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "washdetect-graph";
}

export function numericSuffix(id: string): number {
  const match = /(\d+)$/.exec(id);
  return match ? Number(match[1]) : 0;
}

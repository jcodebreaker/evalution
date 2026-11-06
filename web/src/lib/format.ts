import type { RunSummary, Span } from "./schema";

// Number formats and small derivations the screens share. Units and signs only.

export const score = (value: number) => value.toFixed(2);

export const seconds = (value: number, precise = false) =>
  precise ? `${value.toFixed(1)} s` : `${Math.round(value)} s`;

export function tokens(value: number): string {
  if (value < 1000) return String(Math.round(value));
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  const k = value / 1000;
  return `${k < 10 ? k.toFixed(1) : Math.round(k)}k`;
}

/** `1 test`, `3 tests`. */
export const count = (n: number, one: string, other = `${one}s`) => `${n} ${n === 1 ? one : other}`;

/** A signed change: `+0.04`, `−0.02`, `±0`. */
export function signed(value: number, digits = 2): string {
  const rounded = Number(value.toFixed(digits));
  if (rounded === 0) return "±0";
  return `${rounded > 0 ? "+" : "−"}${Math.abs(rounded).toFixed(digits)}`;
}

/** A signed percentage: `+4%`, `−6%`, `±0%`. */
export function percent(value: number): string {
  const rounded = Math.round(value);
  if (rounded === 0) return "±0%";
  return `${rounded > 0 ? "+" : "−"}${Math.abs(rounded)}%`;
}

/** A time of day, with the date when it is not today. */
export function when(at: number): string {
  const date = new Date(at * 1000);
  const today = new Date().toDateString() === date.toDateString();
  return date.toLocaleString(undefined, {
    ...(today ? {} : { month: "short", day: "numeric" }),
    hour: "numeric",
    minute: "2-digit",
  });
}

/** Runs are numbered in the order they started within their set, so "Run 14" reads naturally. */
export function runNumbers(runs: readonly RunSummary[]): Map<string, number> {
  const ordered = [...runs].sort((a, b) => a.started_at - b.started_at);
  return new Map(ordered.map((run, index) => [run.run, index + 1]));
}

/** How deep each span sits under its parents, for the timeline's indent. */
export function spanDepths(spans: readonly Span[]): Map<string, number> {
  const parents = new Map(spans.map((span) => [span.id, span.parent]));
  const depths = new Map<string, number>();
  for (const span of spans) {
    let depth = 0;
    let parent = span.parent;
    const seen = new Set<string>();
    while (parent && parents.has(parent) && !seen.has(parent)) {
      seen.add(parent);
      depth += 1;
      parent = parents.get(parent) ?? null;
    }
    depths.set(span.id, depth);
  }
  return depths;
}

/** A timeline axis of four even steps that covers every span. */
export function timelineAxis(spans: readonly Span[]): { max: number; ticks: number[] } {
  const end = Math.max(1, ...spans.map((span) => span.start + span.seconds));
  const step = [1, 2, 5, 10, 15, 20, 30, 60, 120, 300, 600].find((s) => s * 4 >= end) ?? end / 4;
  return { max: step * 4, ticks: [0, step, step * 2, step * 3, step * 4] };
}

function matcherText(key: string, value: unknown): string {
  if (value === null || typeof value !== "object") return String(value);
  const matcher = value as Record<string, unknown>;
  if (typeof matcher.contains === "string") return matcher.contains;
  if ("eq" in matcher) return String(matcher.eq);
  if (typeof matcher.re === "string") return `/${matcher.re}/`;
  return key;
}

/** A call of the `tools` block as one short label: its name, first argument and count. */
export function callLabel(call: Record<string, unknown>): string {
  const name = typeof call.name === "string" ? call.name : "?";
  const args = call.args && typeof call.args === "object" ? Object.entries(call.args) : [];
  const first = args[0];
  const arg = first ? ` · ${matcherText(first[0], first[1])}` : "";
  const times = typeof call.times === "number" && call.times > 1 ? ` ×${call.times}` : "";
  return `${name}${arg}${times}`;
}

export const live = (run: Pick<RunSummary, "status" | "analyzing">) =>
  run.status === "running" || run.analyzing;

/** How far a going run is, in tests when the server says, else in tries. */
export function progressText(run: Pick<RunSummary, "progress">, unit = "tests"): string {
  const { tests_done, tests_total, done, total } = run.progress;
  if (tests_total) return `${tests_done ?? 0} of ${tests_total} ${unit}`;
  return `${done} of ${total || "…"} tries`;
}

/** How long ago, in words: `just now`, `5 minutes ago`, `2 hours ago`, `3 days ago`. */
export function ago(at: number, now = Date.now() / 1000): string {
  const minutes = Math.floor((now - at) / 60);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${count(minutes, "minute")} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${count(hours, "hour")} ago`;
  return `${count(Math.floor(hours / 24), "day")} ago`;
}

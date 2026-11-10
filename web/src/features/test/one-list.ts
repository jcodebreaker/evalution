import type {
  Actual,
  Compare,
  PlanItem,
  PlanStep,
  TimelineEntry,
  TimelineLine,
  ToolRow,
  TryOutcome,
  TryResult,
} from "../../lib/schema";
import { TimelineEntrySchema } from "../../lib/schema";
import { turnsOf } from "./try-words";

// A try's "Step by step" as one time-ordered list (docs/judging.md, "Timeline"). The server sends
// it as `steps_timeline`; an older server doesn't, and the same list is built here from the plan
// steps, `tool_rows` and the trajectory, so the page shows the alignment once either way.

/** An entry of the list, with what the page adds: a frame's screenshot address, how far it showed. */
export type Entry = TimelineEntry & {
  shot_url?: string | null;
  part?: { shown: string; missing: string } | null;
};

/** The extra call the server counts against the steps (extras off, or an exact order). */
const EXTRA_COUNTS = "not in the case; extras are off";

const CALL_STATUS: Record<string, TryOutcome> = {
  matched: "passed",
  missing: "failed",
  wrong_args: "failed",
  late: "failed",
  never: "failed",
  unjudged: "unjudged",
};

/** A call's step status as its outcome: an allowed extra, a delegated agent's own call and an
 * automatic call decide nothing. */
export function callStatus(step: string, reason: string | null): TryOutcome | null {
  if (step === "extra") return reason === EXTRA_COUNTS ? "failed" : null;
  return CALL_STATUS[step] ?? null;
}

const make = (fields: Partial<TimelineEntry> & { kind: string }): Entry =>
  TimelineEntrySchema.parse(fields);

const outcomeOf = (actual: Pick<Actual, "passed" | "unjudged"> | undefined): TryOutcome | null =>
  actual ? (actual.unjudged ? "unjudged" : actual.passed ? "passed" : "failed") : null;

/** Failed over couldn't judge over passed; `null` when nothing decided. */
export function worst(statuses: readonly (TryOutcome | null | undefined)[]): TryOutcome | null {
  for (const status of ["failed", "unjudged", "passed"] as const) {
    if (statuses.includes(status)) return status;
  }
  return null;
}

/** A rubric line's number in the test (`rubric-2`, `rubric:2`), for labelling it; else `null`. */
export function rubricLine(id: string): number | null {
  const found = /^rubric[-:](\d+)$/.exec(id);
  return found ? Number(found[1]) : null;
}

/** A line's key in the try's agreement: the plan's `rubric-2` for the server's `rubric:2`. */
export const agreementKey = (id: string) => {
  const line = rubricLine(id);
  return line === null ? id : `rubric-${line}`;
};

function lineOf(item: PlanItem): TimelineLine {
  return {
    id: item.id,
    text: item.text,
    status: outcomeOf(item.actual),
    by: item.judged === "exact" ? "code" : "model",
    role: item.mode === "must" ? "must" : "guide",
    reason: item.actual?.text || null,
  };
}

/** When each span started, from the trace and, failing that, the trajectory. */
function startsOf(shown: Compare): Map<string, number> {
  const starts = new Map<string, number>();
  for (const step of shown.detail?.trajectory ?? []) {
    if (step.span) starts.set(step.span, step.t);
  }
  for (const span of shown.detail?.timeline ?? []) starts.set(span.id, span.start);
  return starts;
}

/** Each expected call against the one made, from `tool_rows`. */
function rowCall(row: ToolRow, shown: Compare, starts: Map<string, number>): Entry {
  const span = row.span ? shown.detail?.timeline.find((entry) => entry.id === row.span) : null;
  return make({
    kind: "call",
    status: callStatus(row.status, row.reason),
    span: row.span,
    at: row.span ? (starts.get(row.span) ?? null) : null,
    actor: span?.helper ? "helper" : "main",
    step: row.status,
    expected: row.expected,
    made: row.actual,
    reason: row.reason,
    automatic: row.status === "automatic",
  });
}

/** A plan step's checks of calls, as calls, when the try has no `tool_rows`. */
function itemCall(item: PlanItem, starts: Map<string, number>): Entry {
  const actual = item.actual;
  const span = actual?.spans[0] ?? null;
  const step =
    item.mode === "never"
      ? "never"
      : item.mode === "allowed"
        ? "extra"
        : !actual
          ? null
          : actual.unjudged
            ? "unjudged"
            : actual.passed
              ? "matched"
              : actual.calls.length
                ? "wrong_args"
                : "missing";
  return make({
    kind: "call",
    status:
      item.mode === "allowed"
        ? null
        : item.mode === "never"
          ? actual
            ? actual.unjudged
              ? "unjudged"
              : actual.passed
                ? null
                : "failed"
            : null
          : outcomeOf(actual),
    span,
    at: span ? (starts.get(span) ?? null) : null,
    actor: "main",
    step,
    expected: item.text,
    made: actual?.calls.length ? actual.calls.join(", ") : null,
    reason: actual && !actual.passed ? actual.text || null : null,
  });
}

/** Which turn each span was made in, from the plan's steps of calls. */
function spanTurns(steps: readonly PlanStep[]): Map<string, number> {
  const found = new Map<string, number>();
  let turn = 0;
  for (const step of steps) {
    if (step.kind === "say" || step.kind === "filler") turn += 1;
    if (step.kind !== "agent") continue;
    for (const item of step.items) {
      for (const span of item.actual?.spans ?? []) found.set(span, Math.max(turn, 1));
    }
  }
  return found;
}

/** `tool_rows` by the turn they belong to: by their span, else the turn of the row before. */
function rowsByTurn(shown: Compare): Map<number, ToolRow[]> {
  const turns = spanTurns(shown.steps);
  const found = new Map<number, ToolRow[]>();
  let last = Math.min(...[...turns.values(), 1]);
  for (const row of shown.tool_rows) {
    const turn = (row.span ? turns.get(row.span) : undefined) ?? last;
    last = turn;
    found.set(turn, [...(found.get(turn) ?? []), row]);
  }
  return found;
}

/** The list built from the plan steps, `tool_rows` and the trajectory: an older server's try. */
export function clientList(shown: Compare): Entry[] {
  const starts = startsOf(shown);
  const byTurn = rowsByTurn(shown);
  const placed = new Set<number>();
  const found: Entry[] = [];
  let turn = 0;
  let pending: { turn: number; step: PlanStep } | null = null;

  const callsOf = (n: number) => {
    if (placed.has(n)) return;
    placed.add(n);
    for (const row of byTurn.get(n) ?? []) found.push(rowCall(row, shown, starts));
  };
  const flush = (reply?: PlanStep) => {
    const lines = reply ? reply.items.map(lineOf) : [];
    if (pending) callsOf(pending.turn);
    if (!pending && !reply) return;
    // The graded lines of a reply already in the list (a state check came between): on that reply.
    const earlier = pending ? null : found.findLast((entry) => entry.kind === "reply");
    if (earlier && reply && !earlier.lines.length && earlier.turn === turn) {
      const must = lines.filter((line) => line.role === "must").map((line) => line.status);
      earlier.lines = lines;
      earlier.status = earlier.error ? "failed" : (worst(must) ?? earlier.status);
      return;
    }
    const said = pending?.step;
    const error = said?.actual && !said.actual.passed && !said.actual.unjudged;
    const text = said?.actual?.reply ?? (reply ? (shown.output?.reply ?? "") : "");
    const decided = worst(lines.filter((line) => line.role === "must").map((line) => line.status));
    found.push(
      make({
        kind: "reply",
        status: error
          ? "failed"
          : (decided ?? (reply && !lines.length ? outcomeOf(reply.actual) : null)),
        actor: "main",
        turn: pending?.turn ?? (turn || null),
        text,
        error: error ? said?.actual?.text || "no reply" : null,
        lines,
      }),
    );
    const page = said?.page;
    if (page) {
      found.push({
        ...make({
          kind: "frame",
          status: page.check?.outcome ?? null,
          at: page.shown_at,
          actor: "page",
          turn: pending?.turn ?? null,
          text: page.shown ?? "",
          instead: page.instead,
          shown_at: page.shown_at,
          shot: page.shot,
          why: page.check?.why || null,
        }),
        shot_url: page.shot_url,
        part: page.part,
      });
    }
    pending = null;
  };

  for (const step of shown.steps) {
    switch (step.kind) {
      case "before": {
        const kept = shown.isolation;
        found.push(
          make({
            kind: "before",
            status: kept
              ? kept.problem
                ? "failed"
                : kept.checked.length
                  ? "passed"
                  : null
              : outcomeOf(step.actual),
            at: 0,
            actor: "code",
            text: step.title || null,
            how: kept?.how ?? null,
            seconds: kept?.restore_seconds ?? null,
            checked: kept?.checked ?? [],
            problem: kept?.problem ?? null,
          }),
        );
        break;
      }
      case "say":
      case "filler":
        flush();
        turn += 1;
        found.push(
          make({
            kind: "message",
            actor: "person",
            turn,
            text: step.message ?? step.title,
            filler: step.kind === "filler",
          }),
        );
        pending = { turn, step };
        break;
      case "agent":
        if (shown.tool_rows.length) callsOf(Math.max(turn, 1));
        else for (const item of step.items) found.push(itemCall(item, starts));
        break;
      case "reply":
        flush(step);
        break;
      case "state":
        flush();
        if (step.items.length) {
          for (const item of step.items) {
            found.push(
              make({
                kind: "state",
                status: outcomeOf(item.actual),
                actor: "code",
                key: item.text,
                made: item.actual?.text || null,
              }),
            );
          }
        } else if (step.actual) {
          found.push(
            make({
              kind: "state",
              status: outcomeOf(step.actual),
              actor: "code",
              key: step.title,
              made: step.actual.text || null,
            }),
          );
        }
        break;
      case "break":
        flush();
        found.push(make({ kind: "break", actor: "person", text: step.title || null }));
        break;
      case "passes":
        flush();
        break;
    }
  }
  flush();
  // Calls of a turn the plan has no step for, and those no turn claimed.
  for (const n of byTurn.keys()) callsOf(n);
  const health = shown.page?.health;
  if (health) {
    found.push(
      make({ kind: "health", status: health.outcome, actor: "page", why: health.why || null }),
    );
  }
  return found;
}

/** The server's list, with each frame's screenshot address and how far it showed, by its turn. */
export function serverList(shown: TryResult, entries: readonly TimelineEntry[]): Entry[] {
  const turns = turnsOf(shown.steps);
  return entries.map((entry) => {
    if (entry.kind !== "frame") return entry;
    const page = turns.find((turn) => turn.n === entry.turn)?.step.page;
    return {
      ...entry,
      shot_url: page?.shot_url ?? null,
      part: page?.part ?? null,
      why: entry.why ?? (page?.check?.why || null),
    };
  });
}

/** The try's one list, and where it came from. */
export function oneList(shown: TryResult): { entries: Entry[]; from: "server" | "client" } {
  if (shown.steps_timeline?.length) {
    return { entries: serverList(shown, shown.steps_timeline), from: "server" };
  }
  return { entries: clientList(shown), from: "client" };
}

/** A line of "Step by step": one entry, or calls or state checks that came together, or a reply
 * with what the page showed of it. */
export type StepRow = {
  kind: "before" | "message" | "calls" | "reply" | "frame" | "state" | "break" | "health";
  entries: Entry[];
  status: TryOutcome | null;
  at: number | null;
};

const ROW_KIND: Record<string, StepRow["kind"]> = {
  before: "before",
  message: "message",
  call: "calls",
  reply: "reply",
  frame: "frame",
  state: "state",
  break: "break",
  health: "health",
};

/** The list in rows: calls in a run together, state checks together, a reply with its frame. */
export function rowsOf(entries: readonly Entry[]): StepRow[] {
  const rows: StepRow[] = [];
  for (const entry of entries) {
    const kind = ROW_KIND[entry.kind];
    if (!kind) continue;
    const last = rows[rows.length - 1];
    const joins =
      last &&
      ((kind === "calls" && last.kind === "calls") ||
        (kind === "state" && last.kind === "state") ||
        (kind === "frame" &&
          last.kind === "reply" &&
          last.entries.length === 1 &&
          last.entries[0]?.turn === entry.turn));
    if (joins && last) last.entries.push(entry);
    else rows.push({ kind, entries: [entry], status: null, at: entry.at });
  }
  for (const row of rows) {
    row.status = worst(row.entries.map((entry) => entry.status));
    row.at = row.entries.find((entry) => entry.at !== null)?.at ?? null;
  }
  return rows;
}

/** The row that holds `span`, else -1. */
export const rowOfSpan = (rows: readonly StepRow[], span: string) =>
  rows.findIndex((row) => row.entries.some((entry) => entry.span === span));

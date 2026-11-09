import { count, live, progressText } from "../../lib/format";
import { joinWords } from "../../lib/isolation";
import type { Health, RunDetail, RunSummary, RunTest, Verdict } from "../../lib/schema";
import { verdictLine } from "../../lib/verdict";
import type { Outcome } from "../../ui/status";

// What the Overview reads from the set's runs, as pure functions over what the server sent: no
// number here is made up, each is counted or taken from a run's own totals.

/** How many runs the Overview's charts and grid show. */
export const LAST = 9;

/** How many runs the Recent runs table lists. */
export const RECENT = 6;

/** Runs, newest first, numbered within their set as the rest of the app numbers them. */
export type Numbered = { runs: readonly RunSummary[]; numbers: ReadonlyMap<string, number> };

const numberOf = ({ numbers }: Numbered, run: string) => numbers.get(run) ?? run;

/** The run ended before any try played: nothing in it says anything of the tests. */
export function didntStart(run: RunSummary): boolean {
  return (
    !live(run) && (run.status === "failed" || run.status === "stopped") && run.progress.done === 0
  );
}

/** The newest run that has finished, analysis and all. */
export const settled = (runs: readonly RunSummary[]) => runs.find((run) => !live(run)) ?? null;

const FAILED: readonly (Verdict | null)[] = ["fail", "flaky"];

/** A run's outcome as one dot: failed when a test failed or the run did; couldn't judge when one
 * couldn't be; passed when every test it judged passed; else nothing to say. */
export function runOutcome(run: RunSummary): Outcome {
  if (live(run) || didntStart(run)) return "none";
  const verdicts = run.tests.map((test) => test.verdict);
  if (run.status === "failed" || verdicts.some((verdict) => FAILED.includes(verdict)))
    return "fail";
  if (verdicts.includes("unjudged")) return "unjudged";
  return verdicts.includes("pass") ? "pass" : "none";
}

const firstLine = (text: string | null) => text?.split("\n")[0]?.trim() || null;

/** A run in one line: what it came to, or why it stopped. */
export function runReason(run: RunSummary): string {
  if (live(run)) return run.analyzing ? "Analyzing" : `Playing: ${progressText(run)}`;
  if (run.interrupted) return "Interrupted: the server restarted";
  const why = firstLine(run.stop_reason) ?? run.tests.find((test) => test.reason)?.reason ?? null;
  if (run.status === "failed") return `Stopped: ${why ?? "it stopped early"}`;
  if (run.status === "stopped") return why ?? "Stopped";
  const failed = run.tests.filter((test) => FAILED.includes(test.verdict));
  const unjudged = run.tests.filter((test) => test.verdict === "unjudged").length;
  const judged = run.tests.filter((test) => test.verdict !== null).length;
  if (!judged) return "Nothing was judged";
  const parts: string[] = [];
  if (failed.length) parts.push(`${namesOf(failed.map((test) => test.name))} failed`);
  if (unjudged) parts.push(`${count(unjudged, "test")} couldn't be judged`);
  return parts.length ? parts.join(" · ") : "All passed";
}

/** "A", "A and B", "A, B and 3 more". */
export function namesOf(names: readonly string[], shown = 2): string {
  if (names.length <= shown + 1) return joinWords(names);
  return `${names.slice(0, shown).join(", ")} and ${names.length - shown} more`;
}

// ---------------------------------------------------------------- the three number cards

/** One run's bar: `live` while it plays; `latest` for the run the card's number is of. */
export type Bar = {
  run: string;
  n: number | string;
  value: number;
  tone: Outcome | "live";
  latest: boolean;
};

/** The runs the charts show, oldest first. */
const recent = (runs: readonly RunSummary[]) => runs.slice(0, LAST).reverse();

export type Passing = {
  run: string;
  n: number | string;
  passed: number;
  total: number;
  /** Against the run it is compared with, when it has one. */
  delta: { by: number; base: string; n: number | string } | null;
  /** Each recent run's share passing, oldest first. */
  bars: Bar[];
};

/** "Passing in the last run": the newest finished run's tests passing, against its base. */
export function passingCard(
  numbered: Numbered,
  detail: Pick<RunDetail, "run" | "base"> | null,
): Passing | null {
  const run = settled(numbered.runs);
  if (!run) return null;
  const base = detail?.run === run.run ? detail.base : null;
  return {
    run: run.run,
    n: numberOf(numbered, run.run),
    passed: run.totals.passed,
    total: run.totals.cases,
    delta: base
      ? {
          by: run.totals.passed - base.totals.passed,
          base: base.run,
          n: numberOf(numbered, base.run),
        }
      : null,
    bars: recent(numbered.runs).map((each) => ({
      run: each.run,
      n: numberOf(numbered, each.run),
      value: each.totals.cases ? each.totals.passed / each.totals.cases : 0,
      tone: live(each) ? "live" : runOutcome(each),
      latest: each.run === run.run,
    })),
  };
}

export type Point = { run: string; n: number | string; value: number };

export type Typical = {
  /** The newest run that played a try, and its middle try's seconds. */
  last: Point;
  /** The run before it that played a try. */
  before: Point | null;
  /** The newest finished run, when it played no try. */
  none: { run: string; n: number | string } | null;
  /** Each recent run's middle try, oldest first: `null` for a run that played none. */
  points: (Point | null)[];
};

const played = (run: RunSummary) => !live(run) && run.progress.done > 0;

/** "Typical try": the middle try's seconds, run by run. */
export function typicalCard(numbered: Numbered): Typical | null {
  const points = recent(numbered.runs).map((run) =>
    played(run)
      ? { run: run.run, n: numberOf(numbered, run.run), value: run.totals.median_seconds }
      : null,
  );
  const shown = points.filter((point): point is Point => point !== null);
  const last = shown.at(-1);
  if (!last) return null;
  const newest = settled(numbered.runs);
  return {
    last,
    before: shown.at(-2) ?? null,
    none: newest && !played(newest) ? { run: newest.run, n: numberOf(numbered, newest.run) } : null,
    points,
  };
}

export type Spent = {
  run: string;
  n: number | string;
  tokens: number;
  /** The middle of what the recent runs that spent anything spent, when two or more did. */
  typical: number | null;
  bars: Bar[];
};

const median = (values: readonly number[]) => {
  const sorted = [...values].sort((a, b) => a - b);
  const half = Math.floor(sorted.length / 2);
  return sorted.length % 2
    ? (sorted[half] ?? 0)
    : ((sorted[half - 1] ?? 0) + (sorted[half] ?? 0)) / 2;
};

/** "Spent": the tokens the newest finished run spent, and each recent run's. */
export function spentCard(numbered: Numbered): Spent | null {
  const run = settled(numbered.runs);
  if (!run) return null;
  const shown = recent(numbered.runs).filter((each) => !live(each));
  const spent = shown.map((each) => each.totals.tokens).filter((tokens) => tokens > 0);
  return {
    run: run.run,
    n: numberOf(numbered, run.run),
    tokens: run.totals.tokens,
    typical: spent.length > 1 ? median(spent) : null,
    bars: shown.map((each) => ({
      run: each.run,
      n: numberOf(numbered, each.run),
      value: each.totals.tokens,
      tone: "none",
      latest: each.run === run.run,
    })),
  };
}

// ---------------------------------------------------------------- each test, run by run

export type CellState = "pass" | "fail" | "unjudged" | "none" | "didnt_start" | "playing";

export type Cell = { run: string; case: string; state: CellState; title: string };

export type Grid = {
  columns: { run: string; n: number | string; latest: boolean }[];
  rows: { case: string; name: string; cells: Cell[] }[];
};

const lowerFirst = (text: string) => text.charAt(0).toLowerCase() + text.slice(1);

function cellOf(run: RunSummary, n: number | string, id: string): Cell {
  const at = `Run ${n}`;
  const cell = (state: CellState, said: string): Cell => ({
    run: run.run,
    case: id,
    state,
    title: `${at}: ${said}`,
  });
  if (didntStart(run)) {
    const why = firstLine(run.stop_reason);
    return cell("didnt_start", why ? `didn't start (${why})` : "didn't start");
  }
  const test: RunTest | undefined = run.tests.find((each) => each.case === id);
  if (!test) return cell("none", "not in this run");
  if (!test.verdict) {
    if (live(run)) return cell("playing", "not played yet");
    return cell("none", test.reason ? `not played (${test.reason})` : "not played");
  }
  const said = lowerFirst(verdictLine(test)) + (test.note ? ` (${test.note})` : "");
  if (test.verdict === "pass") return cell("pass", said);
  if (test.verdict === "unjudged") return cell("unjudged", said);
  return cell("fail", said);
}

/** Each test of the set's last runs, and how it came out in each, oldest run first. */
export function grid(numbered: Numbered, last = LAST): Grid {
  const shown = numbered.runs.slice(0, last);
  const names = new Map<string, string>();
  // Newest first, so a test is named as its newest run names it, and the newest run's order leads.
  for (const run of shown) {
    for (const test of run.tests) if (!names.has(test.case)) names.set(test.case, test.name);
  }
  const oldestFirst = [...shown].reverse();
  const newest = shown[0]?.run;
  return {
    columns: oldestFirst.map((run) => ({
      run: run.run,
      n: numberOf(numbered, run.run),
      latest: run.run === newest,
    })),
    rows: [...names].map(([id, name]) => ({
      case: id,
      name,
      cells: oldestFirst.map((run) => cellOf(run, numberOf(numbered, run.run), id)),
    })),
  };
}

// ---------------------------------------------------------------- needs you

export type Tone = "fail" | "unjudged" | "new";

type Attention = Health["attention"][number];

/** One thing on the Overview that needs the person, as `NeedsYou` shows it. */
export type Need = { id: string; tone: Tone } & (
  | {
      kind: "worse";
      run: string;
      n: number | string;
      count: number;
      base: string;
      baseN: number | string;
      names: string[];
    }
  | {
      kind: "stopped";
      run: string;
      n: number | string;
      interrupted: boolean;
      started: boolean;
      why: string | null;
    }
  | { kind: "page" }
  | { kind: "version"; run: string; n: number | string; why: string | null }
  | { kind: "isolation" }
  | { kind: "change" }
  | { kind: "knowledge"; out: number; flagged: number }
  | { kind: "attention"; item: Attention }
  | { kind: "offer" }
);

export type NeedsInput = Numbered & {
  /** The newest finished run's detail: its base and how it moved. */
  detail: Pick<RunDetail, "run" | "base" | "delta" | "cases"> | null;
  health: Pick<Health, "attention" | "page" | "isolation" | "code_change" | "knowledge"> | null;
  /** The UI-testing offer shows. */
  offer: boolean;
};

const RANK: Record<Verdict, number> = { fail: 0, flaky: 1, pass: 2, unjudged: -1 };

/** The tests that got worse in `detail`'s run against its base, by name. */
export function worseNames(
  detail: NonNullable<NeedsInput["detail"]>,
): { case: string; name: string }[] {
  const before = new Map(detail.base?.tests.map((test) => [test.case, test.verdict]) ?? []);
  return detail.cases.flatMap((row) => {
    const was = before.get(row.case);
    if (!row.verdict || !was || RANK[row.verdict] < 0 || RANK[was] < 0) return [];
    return RANK[row.verdict] < RANK[was] ? [{ case: row.case, name: row.name }] : [];
  });
}

const TONE_ORDER: Record<Tone, number> = { fail: 0, unjudged: 1, new: 2 };

/** What needs the person, most pressing first: what failed, then what may be wrong, then what's
 * new. Each source says itself once: the per-test items the health page lists are left out where
 * a row here already says them. */
export function rankNeeds(input: NeedsInput): Need[] {
  const { runs, detail, health } = input;
  const out: Need[] = [];
  const run = settled(runs);
  const ids = new Set(runs.map((each) => each.run));
  let worseCases = new Set<string>();

  if (run && run.status === "failed") {
    out.push({
      id: `stopped:${run.run}`,
      tone: run.interrupted ? "unjudged" : "fail",
      kind: "stopped",
      run: run.run,
      n: numberOf(input, run.run),
      interrupted: run.interrupted,
      started: !didntStart(run),
      why: firstLine(run.stop_reason) ?? run.tests.find((test) => test.reason)?.reason ?? null,
    });
  }
  if (run && detail?.run === run.run && detail.base && detail.delta?.worse) {
    const worse = worseNames(detail);
    worseCases = new Set(worse.map((test) => test.case));
    out.push({
      id: `worse:${run.run}`,
      tone: "fail",
      kind: "worse",
      run: run.run,
      n: numberOf(input, run.run),
      count: detail.delta.worse,
      base: detail.base.run,
      baseN: numberOf(input, detail.base.run),
      names: worse.length === detail.delta.worse ? worse.map((test) => test.name) : [],
    });
  }
  if (health?.page) {
    out.push({
      id: "page",
      tone: health.page.status === "broken" ? "fail" : "unjudged",
      kind: "page",
    });
  }
  const newest = runs[0];
  if (newest?.version?.verified === false) {
    out.push({
      id: `version:${newest.run}`,
      tone: "unjudged",
      kind: "version",
      run: newest.run,
      n: numberOf(input, newest.run),
      why: newest.version.why_unverified,
    });
  }
  const plan = health?.isolation?.status;
  if (plan === "stale" || plan === "failed") {
    out.push({ id: "isolation", tone: "unjudged", kind: "isolation" });
  }
  const change = health?.code_change;
  if (change && (change.tests.length || change.uncovered.length || !change.read)) {
    out.push({ id: "change", tone: "unjudged", kind: "change" });
  }
  const knowledge = health?.knowledge;
  const flagged = knowledge?.last?.flagged ?? 0;
  if (knowledge && (knowledge.out_of_date || flagged)) {
    out.push({
      id: "knowledge",
      tone: "unjudged",
      kind: "knowledge",
      out: knowledge.out_of_date,
      flagged,
    });
  }
  for (const [at, item] of (health?.attention ?? []).entries()) {
    // Said above: the last run stopping, and the tests that got worse in it.
    if (!item.case && item.action === "see_why" && item.run) continue;
    if (item.case && worseCases.has(item.case) && item.run === run?.run) continue;
    // Another set's run: the Overview is this set's.
    if (item.run && !ids.has(item.run)) continue;
    out.push({ id: `attention:${at}`, tone: attentionTone(item), kind: "attention", item });
  }
  if (input.offer) out.push({ id: "offer", tone: "new", kind: "offer" });

  // Stable: within a tone, the order above.
  return out
    .map((need, at) => ({ need, at }))
    .sort((a, b) => TONE_ORDER[a.need.tone] - TONE_ORDER[b.need.tone] || a.at - b.at)
    .map(({ need }) => need);
}

/** A test failing is a failure; one nobody could judge, or that swings, or may be out of date, is
 * something that may be wrong. */
function attentionTone(item: Attention): Tone {
  if (item.action === "see_why" && item.case && !/couldn't judge$/.test(item.title)) return "fail";
  return "unjudged";
}

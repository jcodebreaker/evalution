import { count, live, progressText } from "../../lib/format";
import type { RunSummary, Verdict } from "../../lib/schema";
import type { Outcome } from "../../ui/status";
import type { Part, Tone } from "./parts";

/** How a run's tests came out, counted: what passed, failed every try, failed some, couldn't be
 * judged, and didn't play. */
export type Tally = {
  total: number;
  passed: number;
  failed: number;
  flaky: number;
  unjudged: number;
  notRun: number;
};

/** The run's tests counted, from its totals and each test's verdict (`null`: didn't play). An
 * older server that lists no verdicts: what didn't pass and wasn't unjudged failed. */
export function tally(
  totals: Pick<RunSummary["totals"], "cases" | "passed" | "unjudged">,
  verdicts: readonly (Verdict | null)[],
): Tally {
  const total = totals.cases;
  const passed = totals.passed;
  const n = (verdict: Verdict) => verdicts.filter((found) => found === verdict).length;
  if (!verdicts.length) {
    const unjudged = totals.unjudged;
    const failed = Math.max(0, total - passed - unjudged);
    return { total, passed, failed, flaky: 0, unjudged, notRun: 0 };
  }
  const failed = n("fail");
  const flaky = n("flaky");
  const unjudged = n("unjudged");
  const notRun = Math.max(0, total - passed - failed - flaky - unjudged);
  return { total, passed, failed, flaky, unjudged, notRun };
}

/** The tally as parts of a bar: passes, failures (every try or some), unjudged, not run. */
export function parts(found: Tally): Part[] {
  return [
    { n: found.passed, tone: "pass" },
    { n: found.failed + found.flaky, tone: "fail" },
    { n: found.unjudged, tone: "unjudged" },
    { n: found.notRun, tone: "none" },
  ];
}

type RunLike = Pick<RunSummary, "status" | "analyzing" | "interrupted">;

/** How a run came out in a word, and its colour: "Passed", "Failed", "Stopped early". */
export function runState(run: RunLike, found: Tally): { word: string; tone: Tone; dot: Outcome } {
  if (run.status === "queued") return { word: "Queued", tone: "neutral", dot: "none" };
  if (run.status === "running") return { word: "Running", tone: "tint", dot: "none" };
  if (run.analyzing) return { word: "Analyzing", tone: "tint", dot: "none" };
  if (run.interrupted) return { word: "Interrupted", tone: "neutral", dot: "none" };
  if (run.status === "stopped") return { word: "Stopped", tone: "neutral", dot: "none" };
  if (run.status === "failed") return { word: "Stopped early", tone: "fail", dot: "fail" };
  if (found.failed || found.flaky) return { word: "Failed", tone: "fail", dot: "fail" };
  if (found.total && found.passed === found.total) {
    return { word: "Passed", tone: "pass", dot: "pass" };
  }
  if (found.unjudged) return { word: "Couldn't judge", tone: "unjudged", dot: "unjudged" };
  return { word: "Done", tone: "neutral", dot: "none" };
}

/** What a finished run's tests came to, in a line: "2 failed, 1 not run"; "All passed". */
export function tallyWords(found: Tally): string {
  if (found.total && found.passed === found.total) return "All passed";
  const said = [
    found.failed ? `${found.failed} failed` : null,
    found.flaky ? `${found.flaky} failed some tries` : null,
    found.unjudged ? `${found.unjudged} couldn't judge` : null,
    found.notRun ? `${found.notRun} not run` : null,
  ].filter(Boolean);
  return said.length ? said.join(", ") : `${found.passed} of ${found.total} passed`;
}

/** A run in the list, in a line: why it stopped, or what its tests came to. */
export function runLine(
  run: Pick<
    RunSummary,
    "status" | "analyzing" | "interrupted" | "stop_reason" | "progress" | "scope" | "cases"
  >,
  found: Tally,
  /** For a queued run, the label of the run it waits for ("Run 8"), when known. */
  after: string | null = null,
): string {
  if (run.status === "queued") return queuedWords(after);
  if (live(run)) return run.analyzing ? "Analyzing what went wrong" : progressText(run);
  const some =
    run.scope === "some" && run.cases.length ? ` · ${count(run.cases.length, "test")}` : "";
  const why = run.stop_reason?.split("\n")[0]?.trim();
  if (run.interrupted) return "Interrupted: the server restarted under it";
  if (run.status === "failed") return why ? `Stopped: ${why}` : "Didn't finish";
  if (run.status === "stopped") return why ? `Stopped: ${why}` : `Stopped${some}`;
  return `${tallyWords(found)}${some}`;
}

/** A queued run in a line: "Queued · after Run 8", or that it waits for the app. */
export const queuedWords = (after: string | null) =>
  after ? `Queued · after ${after}` : "Queued · plays when the app is free";

/** How long a run took, roughly: "35 s", "6 min"; `null` while it goes, or when nothing says. */
export function took(run: Pick<RunSummary, "started_at" | "ended_at">): string | null {
  if (run.ended_at === null || run.ended_at < run.started_at) return null;
  const seconds = run.ended_at - run.started_at;
  return seconds < 90 ? `${Math.max(1, Math.round(seconds))} s` : `${Math.round(seconds / 60)} min`;
}

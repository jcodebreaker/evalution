import { useEffect, useRef } from "react";
import type { Scope } from "../../lib/api";
import { api } from "../../lib/api";
import { live } from "../../lib/format";
import type { Place } from "../../lib/router";
import type { CaseRow, RunSummary } from "../../lib/schema";
import { POLL_MS, useLoad, useVisiblePoll } from "../../lib/use-load";
import { isBenchmark } from "../benchmarks/BenchmarksView";

/**
 * Where the page moves so the run shown sits in its own app and test set: the sidebar, the run
 * numbers and what the run is compared with all follow the page's set. `null` when it's there.
 */
export function followRun(
  place: Pick<Place, "view" | "run">,
  run: Pick<RunSummary, "run" | "target" | "set"> | null,
  scope: { target: string | null; set: string | null },
): Partial<Place> | null {
  if (place.view !== "runs" || !run || run.run !== place.run || isBenchmark(run.set)) return null;
  const target = run.target ?? scope.target;
  if (run.set === scope.set && target === scope.target) return null;
  return { target, set: run.set };
}

/** The run shown, and the test shown with the run whose tries it shows. */
export function useShown(
  place: Place,
  scope: Scope | null,
  scopeKey: string | null,
  rows: CaseRow[],
  runs: RunSummary[],
  agentMessages: number,
) {
  const row = rows.find((entry) => entry.case === place.case) ?? null;
  const shownRun = place.view === "runs" ? place.run : null;
  const testRun = place.case ? (place.run ?? row?.last_run ?? null) : null;
  const [detail, reloadDetail] = useLoad(
    shownRun && !place.case ? `run:${shownRun}:${place.base ?? ""}` : null,
    () => api.run(shownRun ?? "", place.base),
  );
  const [file, reloadFile] = useLoad(
    scopeKey && place.case ? `file:${scopeKey}:${place.case}:${rows.length}` : null,
    () => api.caseFile(scope as Scope, place.case ?? ""),
  );
  // The test writer may have just changed the test on screen.
  useEffect(() => {
    if (agentMessages && place.case) void reloadFile();
  }, [agentMessages, place.case, reloadFile]);
  const shownLive = runs.some((run) => run.run === shownRun && live(run));
  const testLive = runs.some((run) => run.run === testRun && live(run));
  useVisiblePoll(reloadDetail, POLL_MS, shownLive && !place.case);
  const wasLive = useRef(false);
  useEffect(() => {
    if (wasLive.current && !shownLive) void reloadDetail();
    wasLive.current = shownLive;
  }, [shownLive, reloadDetail]);

  return { row, testRun, detail, reloadDetail, file, reloadFile, testLive };
}

export type Shown = ReturnType<typeof useShown>;

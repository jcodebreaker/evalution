import { useEffect, useRef } from "react";
import type { Scope } from "../../lib/api";
import { api } from "../../lib/api";
import { live } from "../../lib/format";
import type { Place } from "../../lib/router";
import type { CaseRow, RunSummary } from "../../lib/schema";
import { POLL_MS, useLoad, useVisiblePoll } from "../../lib/use-load";

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

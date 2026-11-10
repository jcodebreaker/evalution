import { Search, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";
import type { Scope } from "../../lib/api";
import { api } from "../../lib/api";
import { cn } from "../../lib/cn";
import { live } from "../../lib/format";
import type { CaseRow, PassMarks, RunSummary, Verdict } from "../../lib/schema";
import { ready, useLoad } from "../../lib/use-load";
import { Button } from "../../ui/button";
import { MoreMenu, Picker } from "../../ui/menu";
import { RunAll } from "../runs/RunAll";
import { PageHead, Panel, Seg } from "../shared/kit";
import { Page } from "../shared/layout";
import { Composer } from "./Composer";
import { PassMarksDialog } from "./PassMarksDialog";
import { Suggested } from "./Suggested";
import { TEST_COLUMNS, TestRow, WIDE_ONLY } from "./TestRow";

/** How the tests play, in a sentence: every test of an app plays one way. */
export function playsLine(target: string, rows: readonly CaseRow[]): string {
  const page = rows.filter((row) => row.plays === "page").length;
  if (page === rows.length) {
    return `Played through ${target}'s page: each test types each message there, and checks it shows.`;
  }
  if (page === 0)
    return `Played through ${target}'s API: each test sends its messages straight to it.`;
  return `Page tests type into ${target}'s page; API tests send straight to its API.`;
}

export type TestFilter = "all" | "failing" | "flaky" | "stale";

const FILTERS: { value: TestFilter; label: string; keep: (row: CaseRow) => boolean }[] = [
  { value: "all", label: "All", keep: () => true },
  { value: "failing", label: "Failing", keep: (row) => row.verdict === "fail" },
  { value: "flaky", label: "Flaky", keep: (row) => row.verdict === "flaky" },
  { value: "stale", label: "May be stale", keep: (row) => !!row.stale },
];

/** Each test's verdict in the last `size` finished runs, oldest first; `null` where it didn't play. */
export function lastRuns(runs: readonly RunSummary[], size = 10): Map<string, (Verdict | null)[]> {
  const done = runs
    .filter((run) => !live(run))
    .sort((a, b) => a.started_at - b.started_at)
    .slice(-size);
  const found = new Map<string, (Verdict | null)[]>();
  done.forEach((run, index) => {
    for (const test of run.tests) {
      const cells = found.get(test.case) ?? Array(done.length).fill(null);
      cells[index] = test.verdict;
      found.set(test.case, cells);
    }
  });
  return found;
}

/** The page moved to another test set, as the sidebar's picker moves it. */
function openSet(set: string) {
  const params = new URLSearchParams(window.location.search);
  params.set("set", set);
  for (const key of ["run", "case", "base"]) params.delete(key);
  window.history.pushState(null, "", `${window.location.pathname}?${params}`);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

/**
 * The tests: find one, filter to the failing, flaky or maybe stale; the test set and pass marks;
 * a row per test with its last ten runs and a run button on every row; then the tests suggested
 * from what was learned. "New test" opens the composer that hands it to the test writer.
 */
export function TestsView({
  scope,
  rows,
  passMarks,
  going,
  onAsk,
  onPassMarks,
  onGoing,
  onRunAll,
  onRun,
  onOpen,
}: {
  scope: Scope;
  rows: CaseRow[];
  passMarks: PassMarks;
  going: boolean;
  onAsk: (text: string) => Promise<void>;
  onPassMarks: () => Promise<unknown>;
  onGoing: () => void;
  onRunAll: () => void;
  onRun: (row: CaseRow) => void;
  onOpen: (row: CaseRow) => void;
}) {
  const [marking, setMarking] = useState(false);
  const [writing, setWriting] = useState(false);
  const [find, setFind] = useState("");
  const [filter, setFilter] = useState<TestFilter>("all");
  const key = `${scope.target}/${scope.set}`;
  const [runsLoad] = useLoad(`tests-runs:${key}`, () => api.runs(scope));
  const [setsLoad] = useLoad(`tests-sets:${scope.target}`, () => api.sets(scope.target));
  const history = useMemo(() => lastRuns(ready(runsLoad) ?? []), [runsLoad]);
  const sets = ready(setsLoad) ?? [];

  const words = find.trim().toLowerCase();
  const kept = FILTERS.find((entry) => entry.value === filter)?.keep ?? (() => true);
  const shown = rows.filter(
    (row) =>
      kept(row) && (!words || `${row.name} ${row.about ?? ""}`.toLowerCase().includes(words)),
  );
  const composing = writing || !rows.length;

  return (
    <>
      <Page full>
        <PageHead
          title="Tests"
          actions={
            <>
              <Button
                variant="secondary"
                size="lg"
                aria-expanded={composing}
                onClick={() => setWriting((now) => !now)}
              >
                <Sparkles />
                New test
              </Button>
              {rows.length ? <RunAll going={going} onGoing={onGoing} onRun={onRunAll} /> : null}
            </>
          }
        />
        {composing ? <Composer target={scope.target} onAsk={onAsk} /> : null}
        {rows.length ? (
          <>
            <div className="flex flex-wrap items-center gap-2.5">
              <label className="flex h-10 max-w-[320px] min-w-0 flex-[1_1_220px] items-center gap-2 rounded-[10px] border border-rule-strong bg-group px-3 focus-within:border-tint focus-within:shadow-[0_0_0_3px_var(--tint-soft)]">
                <Search className="size-[15px] shrink-0 text-ink-3" aria-hidden />
                <input
                  value={find}
                  onChange={(event) => setFind(event.target.value)}
                  placeholder="Find a test"
                  aria-label="Find a test"
                  className="min-w-0 flex-1 border-0 bg-transparent outline-0"
                />
              </label>
              <Seg
                label="Show"
                value={filter}
                onChange={setFilter}
                options={FILTERS.map((entry) => ({
                  value: entry.value,
                  label: entry.label,
                  count: rows.filter(entry.keep).length,
                }))}
              />
              <span className="flex-1" />
              {sets.length > 1 ? (
                <span className="inline-flex h-9 items-center gap-1 rounded-[10px] border border-rule-strong bg-group ps-3 pe-1 text-sm">
                  Test set
                  <Picker
                    label="Test set"
                    value={sets.find((entry) => entry.set === scope.set)?.name ?? scope.set}
                    options={sets.map((entry) => ({ value: entry.set, label: entry.name }))}
                    onPick={(set) => {
                      if (set !== scope.set) openSet(set);
                    }}
                  />
                </span>
              ) : null}
              <MoreMenu
                label="More"
                className="size-9 rounded-[10px] border border-rule-strong bg-group"
                actions={[{ label: "Pass marks", onClick: () => setMarking(true) }]}
              />
            </div>
            <Panel aria-label="Tests">
              <div
                className={cn(
                  TEST_COLUMNS,
                  "border-b border-rule pe-5 text-[13px] font-medium whitespace-nowrap text-ink-3",
                )}
                aria-hidden
              >
                <span className="py-2.5 ps-5">Test</span>
                <span className={WIDE_ONLY} title={playsLine(scope.target, rows)}>
                  Plays
                </span>
                <span className={WIDE_ONLY}>Last 10 runs</span>
                <span className="text-center">Tries</span>
                <span className={cn("text-end", WIDE_ONLY)}>Time</span>
                <span />
              </div>
              {shown.map((row) => (
                <TestRow
                  key={row.case}
                  row={row}
                  going={going}
                  history={history.get(row.case)}
                  onOpen={() => onOpen(row)}
                  onRun={() => onRun(row)}
                />
              ))}
              {shown.length ? null : (
                <p className="m-0 px-5 py-6 text-center text-ink-3">No test here</p>
              )}
            </Panel>
          </>
        ) : null}
        <Suggested target={scope.target} onAsk={onAsk} />
      </Page>
      {marking ? (
        <PassMarksDialog
          scope={scope}
          marks={passMarks}
          onClose={() => setMarking(false)}
          onSaved={async () => {
            await onPassMarks();
            setMarking(false);
          }}
        />
      ) : null}
    </>
  );
}

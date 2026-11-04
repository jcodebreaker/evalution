import type { ReactNode } from "react";
import { useState } from "react";
import { live, progressText, when } from "../../lib/format";
import type { RunSummary } from "../../lib/schema";
import { Spinner } from "../../ui/loading";
import { Group, GroupHeading, TextLink } from "../shared/group";
import { Centered, Page } from "../shared/layout";
import type { Entry } from "./RunRow";
import { RunRow } from "./RunRow";

/** How many runs a test shows before "Show all". */
const SHOWN_RUNS = 5;

type TestRuns = { id: string; name: string; entries: Entry[] };

/** Each test with its runs, the most recently run test first; a run counts for every test it played. */
function byTest(runs: RunSummary[]): TestRuns[] {
  const groups = new Map<string, TestRuns>();
  for (const run of runs) {
    for (const test of run.tests) {
      if (run.status === "done" && !test.tries && test.verdict === null) continue;
      const group = groups.get(test.case) ?? {
        id: test.case,
        name: test.name,
        entries: [],
      };
      group.entries.push({ run, test });
      groups.set(test.case, group);
    }
  }
  return [...groups.values()];
}

/** The set's runs, grouped by the test they played, newest first. */
export function RunsView({
  runs,
  label,
  empty,
  onOpen,
  benchmarks = [],
  onBenchmark,
}: {
  /** Newest first. */
  runs: RunSummary[];
  label: (run: string) => string;
  /** Shown instead when the set has no runs: the way to start one. */
  empty: ReactNode;
  onOpen: (run: string, test: string) => void;
  /** Benchmark runs, shown apart: they're no part of the set's own numbers. */
  benchmarks?: RunSummary[];
  onBenchmark?: (run: string) => void;
}) {
  const groups = byTest(runs);
  if (!groups.length && !benchmarks.length) {
    return (
      <Centered>
        <p className="m-0 text-[15px] font-semibold text-foreground">No runs yet</p>
        {empty}
      </Centered>
    );
  }
  return (
    <Page>
      {benchmarks.length ? (
        <section>
          <GroupHeading title="Benchmarks" />
          <Group>
            {benchmarks.map((run) => (
              <button
                key={run.run}
                type="button"
                onClick={() => onBenchmark?.(run.run)}
                className="flex w-full flex-wrap items-center gap-x-3 gap-y-0.5 border-t border-separator px-4 py-2.5 text-start text-[13.5px] first:border-t-0 hover:bg-selection focus-visible:outline-2 focus-visible:outline-ring"
              >
                <span className="font-semibold">
                  LongMemEval {run.set.replace("longmemeval-", "")}
                </span>
                <span className="text-ink-2">{when(run.started_at)}</span>
                <span className="ms-auto flex items-center gap-2 tabular-nums text-ink-2">
                  {live(run) ? <Spinner /> : null}
                  {live(run)
                    ? progressText(run)
                    : `${run.totals.passed} of ${run.totals.cases} passed`}
                </span>
              </button>
            ))}
          </Group>
        </section>
      ) : null}
      {groups.map((group) => (
        <TestGroup key={group.id} group={group} label={label} onOpen={onOpen} />
      ))}
    </Page>
  );
}

function TestGroup({
  group,
  label,
  onOpen,
}: {
  group: TestRuns;
  label: (run: string) => string;
  onOpen: (run: string, test: string) => void;
}) {
  const [all, setAll] = useState(false);
  const shown = all ? group.entries : group.entries.slice(0, SHOWN_RUNS);
  const hidden = group.entries.length - shown.length;
  return (
    <section>
      <GroupHeading
        title={group.name}
        aside={hidden > 0 ? <TextLink onClick={() => setAll(true)}>Show all</TextLink> : null}
      />
      <Group>
        {shown.map((entry) => (
          <RunRow
            key={entry.run.run}
            entry={entry}
            label={label(entry.run.run)}
            onOpen={() => onOpen(entry.run.run, group.id)}
          />
        ))}
      </Group>
    </section>
  );
}

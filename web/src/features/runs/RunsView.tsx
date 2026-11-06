import type { ReactNode } from "react";
import { Fragment, useState } from "react";
import { live, progressText, when } from "../../lib/format";
import type { RunSummary } from "../../lib/schema";
import { Spinner } from "../../ui/loading";
import { benchmarkName } from "../benchmarks/Glance";
import { Group, GroupHeading, TextLink } from "../shared/group";
import { Centered, Page, Toolbar, ToolbarTitle } from "../shared/layout";
import type { Entry } from "./RunRow";
import { RunRow } from "./RunRow";

/** How many runs a test shows before "Show all". */
const SHOWN_RUNS = 5;

type TestRuns = { id: string; name: string; entries: Entry[] };
type Place = { key: string; label: string; groups: TestRuns[] };

/** The runs by where they played: hosted first, then each local place, unnamed before named. */
function byPlace(runs: RunSummary[]): Place[] {
  const places = new Map<string, { label: string; runs: RunSummary[] }>();
  for (const run of runs) {
    const { where, name } = run.environment;
    const key = `${where === "hosted" ? 0 : 1}${name === null ? 0 : 1}${name ?? ""}`;
    const place = places.get(key) ?? {
      label: name ?? (where === "hosted" ? "Hosted" : "Local"),
      runs: [],
    };
    place.runs.push(run);
    places.set(key, place);
  }
  return [...places.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, place]) => ({ key, label: place.label, groups: byTest(place.runs) }))
    .filter((place) => place.groups.length);
}

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
  const places = byPlace(runs);
  // A benchmark run that stopped before playing anything has nothing behind it.
  const played = benchmarks.filter((run) => live(run) || run.tests.some((test) => test.tries));
  if (!places.length && !played.length) {
    return (
      <Centered>
        <p className="m-0 text-[15px] font-semibold text-foreground">No runs yet</p>
        {empty}
      </Centered>
    );
  }
  return (
    <>
      <Toolbar>
        <ToolbarTitle title="Runs" />
      </Toolbar>
      <Page>
        {played.length ? (
          <section>
            <GroupHeading title="Benchmarks" />
            <Group>
              {played.map((run) => (
                <button
                  key={run.run}
                  type="button"
                  onClick={() => onBenchmark?.(run.run)}
                  className="flex w-full flex-wrap items-center gap-x-3 gap-y-0.5 border-t border-separator px-4 py-2.5 text-start text-[13.5px] first:border-t-0 hover:bg-selection focus-visible:outline-2 focus-visible:outline-ring"
                >
                  <span className="font-medium">{benchmarkName(run.set)}</span>
                  <span className="text-ink-2">{when(run.started_at)}</span>
                  <span className="ms-auto flex items-center gap-2 tabular-nums text-ink-2">
                    {live(run) ? <Spinner /> : null}
                    {live(run)
                      ? progressText(run)
                      : run.status === "done"
                        ? `${run.totals.passed} of ${run.totals.cases} right`
                        : `Didn't finish · ${run.totals.passed} of ${run.totals.cases} right`}
                  </span>
                </button>
              ))}
            </Group>
          </section>
        ) : null}
        {places.map((place) => {
          const groups = place.groups.map((group) => (
            <TestGroup key={group.id} group={group} label={label} onOpen={onOpen} />
          ));
          // One place is every run: nothing to tell apart, so no heading.
          if (places.length === 1) return <Fragment key={place.key}>{groups}</Fragment>;
          return (
            <section key={place.key} className="flex flex-col gap-6">
              <h2 className="m-0 px-1 text-[11.5px] font-semibold tracking-[0.02em] text-ink-3 uppercase">
                {place.label}
              </h2>
              {groups}
            </section>
          );
        })}
      </Page>
    </>
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

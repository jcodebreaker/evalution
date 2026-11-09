import { ChevronDown } from "lucide-react";
import type { ReactNode } from "react";
import { useState } from "react";
import { live, progressText, when } from "../../lib/format";
import type { RunSummary } from "../../lib/schema";
import { Spinner } from "../../ui/loading";
import { benchmarkName } from "../benchmarks/Glance";
import { Centered, Page } from "../shared/layout";
import { Card, CardHead } from "./parts";
import { RunColumns, RunRow } from "./RunRow";
import { versionChip } from "./Version";

type Place = { key: string; label: string; runs: RunSummary[] };

/** The runs by where they played: hosted first, then each local place, unnamed before named. */
function byPlace(runs: RunSummary[]): Place[] {
  const places = new Map<string, Place>();
  for (const run of runs) {
    const { where, name } = run.environment;
    const key = `${where === "hosted" ? 0 : 1}${name === null ? 0 : 1}${name ?? ""}`;
    const place = places.get(key) ?? {
      key,
      label: name ?? (where === "hosted" ? "Hosted" : "Local"),
      runs: [],
    };
    place.runs.push(run);
    places.set(key, place);
  }
  return [...places.values()].sort((a, b) => a.key.localeCompare(b.key));
}

/** The versions the runs played, newest first, as their chips say them: "feature-x @ a1b2c3d".
 * Runs that didn't record one aren't any of them. */
export function versionsOf(runs: RunSummary[]): string[] {
  return [...new Set(runs.map((run) => versionChip(run.version)).filter((said) => said !== null))];
}

/** The set's runs, newest first: each a row with its dot, "Run n" and why, a bar of how its
 * tests came out, the code it played, its typical try, tokens and when it started. More than one
 * version played: a filter by version. */
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
  /** Opens a run; at one of its tests when `test` names one. */
  onOpen: (run: string, test: string | null) => void;
  /** Benchmark runs, shown apart: they're no part of the set's own numbers. */
  benchmarks?: RunSummary[];
  onBenchmark?: (run: string) => void;
}) {
  const [version, setVersion] = useState("");
  const versions = versionsOf(runs);
  // A version no run played any more (the list reloaded) shows them all.
  const only = versions.includes(version) ? version : "";
  const places = byPlace(only ? runs.filter((run) => versionChip(run.version) === only) : runs);
  // A benchmark run that stopped before playing anything has nothing behind it.
  const played = benchmarks.filter((run) => live(run) || run.tests.some((test) => test.tries));
  if (!places.length && !played.length && !only) {
    return (
      <Centered>
        <p className="m-0 text-[15px] font-semibold text-foreground">No runs yet</p>
        {empty}
      </Centered>
    );
  }
  return (
    <Page wide>
      <div className="flex flex-wrap items-end gap-x-5 gap-y-3">
        <h1 className="m-0 min-w-0 flex-1 text-[30px] leading-[1.15] font-semibold tracking-[-0.01em] max-[560px]:text-[24px]">
          Runs
        </h1>
        {versions.length > 1 ? (
          <label className="relative inline-flex h-8 items-center gap-1.5 rounded-[6px] border border-rule-strong bg-group ps-3 pe-8 text-[13px] text-ink-2 focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-tint">
            Code
            <select
              aria-label="Version"
              value={only}
              onChange={(event) => setVersion(event.target.value)}
              className="max-w-[240px] cursor-default appearance-none truncate bg-transparent font-mono text-[12px] text-foreground outline-none"
            >
              <option value="">All versions</option>
              {versions.map((said) => (
                <option key={said} value={said}>
                  {said}
                </option>
              ))}
            </select>
            <ChevronDown
              aria-hidden
              className="pointer-events-none absolute end-2.5 size-3.5 text-ink-3"
            />
          </label>
        ) : null}
      </div>
      {played.length ? (
        <Card className="overflow-hidden">
          <CardHead title="Benchmarks" />
          <div className="mt-3">
            {played.map((run) => (
              <button
                key={run.run}
                type="button"
                onClick={() => onBenchmark?.(run.run)}
                className="flex w-full flex-wrap items-center gap-x-3 gap-y-0.5 border-t border-separator px-5 py-3 text-start text-[14px] hover:bg-sunk focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-tint"
              >
                <span className="font-medium">{benchmarkName(run.set)}</span>
                <span className="text-ink-3">{when(run.started_at)}</span>
                <span className="ms-auto flex items-center gap-2 text-ink-2 tabular-nums">
                  {live(run) ? <Spinner /> : null}
                  {live(run)
                    ? progressText(run)
                    : run.status === "done"
                      ? `${run.totals.passed} of ${run.totals.cases} right`
                      : `Didn't finish · ${run.totals.passed} of ${run.totals.cases} right`}
                </span>
              </button>
            ))}
          </div>
        </Card>
      ) : null}
      {places.length ? (
        places.map((place) => (
          <section key={place.key} className="flex flex-col gap-2">
            {/* One place is every run: nothing to tell apart, so no heading. */}
            {places.length > 1 ? (
              <h2 className="m-0 px-1 text-[14px] font-semibold text-ink-2">{place.label}</h2>
            ) : null}
            <Card className="@container overflow-x-auto">
              <div className="min-w-[400px]">
                <RunColumns />
                {place.runs.map((run) => (
                  <RunRow
                    key={run.run}
                    run={run}
                    label={label(run.run)}
                    onOpen={() => onOpen(run.run, null)}
                  />
                ))}
              </div>
            </Card>
          </section>
        ))
      ) : (
        <p className="m-0 px-1 text-[14px] text-ink-2">No run played this version.</p>
      )}
    </Page>
  );
}

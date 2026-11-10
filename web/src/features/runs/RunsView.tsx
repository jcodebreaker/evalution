import { ChevronDown, Gauge } from "lucide-react";
import type { ReactNode } from "react";
import { useState } from "react";
import { api } from "../../lib/api";
import { cn } from "../../lib/cn";
import { live, progressText, tokens, when } from "../../lib/format";
import type { RunSummary } from "../../lib/schema";
import { Button } from "../../ui/button";
import { benchmarkName } from "../benchmarks/Glance";
import { Dot, Meter, PageHead } from "../shared/kit";
import { Centered, Page } from "../shared/layout";
import { Card } from "./parts";
import { RUN_COLUMNS, RunColumns, RunRow } from "./RunRow";
import { VersionChip, versionChip } from "./Version";

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

/** What a going run plays now, in a line: "2 of 3 tests · Adds to the cart once · try 1 · grading". */
export function goingWords(run: RunSummary): string {
  if (run.analyzing) return "Analyzing what went wrong";
  const current = run.progress.current;
  return [
    progressText(run),
    current?.name,
    current ? `try ${current.try}` : null,
    current?.stage ?? null,
  ]
    .filter(Boolean)
    .join(" · ");
}

/** The run playing now: how far it is, what it plays, and Stop and Open. */
function LiveRun({ run, label, onOpen }: { run: RunSummary; label: string; onOpen: () => void }) {
  const [stopping, setStopping] = useState(false);
  const { done, total } = run.progress;
  return (
    <Card aria-label={`${label}, playing`} data-testid="live-run">
      <div className="flex flex-wrap items-center gap-4 p-5">
        <Dot verdict="go" />
        <b className="font-semibold">{label}</b>
        <VersionChip version={run.version} />
        <div className="min-w-0 flex-[1_1_200px]">
          <Meter value={total ? done / total : 0} tone="go" label={`${done} of ${total} tries`} />
          <p className="m-0 mt-1.5 truncate text-sm text-ink-3">{goingWords(run)}</p>
        </div>
        {run.status === "running" ? (
          <Button
            variant="secondary"
            disabled={stopping}
            onClick={() => {
              setStopping(true);
              void api.stop(run.run).catch(() => setStopping(false));
            }}
          >
            {stopping ? "Stopping" : "Stop"}
          </Button>
        ) : null}
        <Button variant="secondary" onClick={onOpen}>
          Open
        </Button>
      </div>
    </Card>
  );
}

/** A benchmark run as a row of the same table: its name, how many it got right, its code, its
 * time and tokens. */
function BenchmarkRow({ run, onOpen }: { run: RunSummary; onOpen: () => void }) {
  const going = live(run);
  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        RUN_COLUMNS,
        "w-full border-rule px-5 py-3.5 text-start not-first:border-t hover:bg-sunk focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-tint",
      )}
    >
      <span className="flex min-w-0 items-center gap-3">
        <Gauge aria-hidden className="size-[18px] shrink-0 text-ink-3" />
        <span className="flex min-w-0 flex-col">
          <b className="font-medium">{benchmarkName(run.set)}</b>
          <small className="truncate text-sm text-ink-3">
            {going ? goingWords(run) : run.status === "done" ? "Benchmark" : "Didn't finish"}
          </small>
        </span>
      </span>
      <span className="font-mono text-[13px] tabular-nums">
        {going ? "–" : `${run.totals.passed} of ${run.totals.cases} right`}
      </span>
      <span className="min-w-0 @max-[719px]:hidden">
        <VersionChip version={run.version} />
      </span>
      <span className="text-end font-mono text-[13px] text-ink-3 tabular-nums">–</span>
      <span className="text-end font-mono text-[13px] text-ink-3 tabular-nums @max-[719px]:hidden">
        {tokens(run.totals.tokens)}
      </span>
      <span className="text-end text-[13px] whitespace-nowrap text-ink-3 tabular-nums">
        {when(run.started_at)}
      </span>
    </button>
  );
}

/** The set's runs, newest first: the run playing now with its progress and Stop; then a table,
 * each run a row with its dot, "Run n" and why, a bar of how its tests came out, the code it
 * played, its typical try, tokens and when it started; benchmark runs as rows of their own. More
 * than one version played: a filter by version. */
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
  /** The way to start one: shown in the head, and alone when the set has no runs. */
  empty: ReactNode;
  /** Opens a run; at one of its tests when `test` names one. */
  onOpen: (run: string, test: string | null) => void;
  /** Benchmark runs: rows of their own, no part of the set's own numbers. */
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
  const going = runs.filter(live);
  if (!places.length && !played.length && !only) {
    return (
      <Centered>
        <p className="m-0 font-semibold text-foreground">No runs yet</p>
        {empty}
      </Centered>
    );
  }
  return (
    <Page full>
      <PageHead
        title="Runs"
        actions={
          <>
            {versions.length > 1 ? (
              <label className="relative inline-flex h-9 items-center gap-1.5 rounded-[10px] border border-rule-strong bg-group ps-3 pe-8 text-sm text-ink-2 focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-tint">
                Code
                <select
                  aria-label="Version"
                  value={only}
                  onChange={(event) => setVersion(event.target.value)}
                  className="max-w-[240px] cursor-default appearance-none truncate bg-transparent font-mono text-[13px] text-foreground outline-none"
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
            {empty}
          </>
        }
      />
      {going.map((run) => (
        <LiveRun
          key={run.run}
          run={run}
          label={label(run.run)}
          onOpen={() => onOpen(run.run, null)}
        />
      ))}
      {places.length ? (
        places.map((place, index) => (
          <section key={place.key} className="flex flex-col gap-2">
            {/* One place is every run: nothing to tell apart, so no heading. */}
            {places.length > 1 ? (
              <h2 className="m-0 px-1 text-sm font-semibold text-ink-2">{place.label}</h2>
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
                {index === places.length - 1
                  ? played.map((run) => (
                      <BenchmarkRow key={run.run} run={run} onOpen={() => onBenchmark?.(run.run)} />
                    ))
                  : null}
              </div>
            </Card>
          </section>
        ))
      ) : (
        <>
          {only ? <p className="m-0 px-1 text-sm text-ink-2">No run played this version.</p> : null}
          {played.length ? (
            <Card className="@container overflow-x-auto" aria-label="Benchmarks">
              <div className="min-w-[400px]">
                <RunColumns />
                {played.map((run) => (
                  <BenchmarkRow key={run.run} run={run} onOpen={() => onBenchmark?.(run.run)} />
                ))}
              </div>
            </Card>
          ) : null}
        </>
      )}
    </Page>
  );
}

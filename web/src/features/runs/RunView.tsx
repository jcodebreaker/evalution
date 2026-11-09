import { count, live, tokens, when } from "../../lib/format";
import type { CaseRow, RunDetail, RunSummary } from "../../lib/schema";
import type { Load } from "../../lib/use-load";
import { PAGE_ONLY_RUN } from "../../lib/verdict";
import { Button } from "../../ui/button";
import { Fold } from "../shared/group";
import { Centered, Page } from "../shared/layout";
import { Comparison, MORE_TRIES } from "./Comparison";
import { Scores } from "./Details";
import { parts, runState, type Tally, tally, took } from "./outcome";
import { Progress } from "./Progress";
import { Card, Chip, Notice, PartsBar, Pill, Ring, Swatch } from "./parts";
import { Results } from "./Results";
import { copyWords, playsWords, RunFacts } from "./RunFacts";
import { NotBuilt, VersionChip } from "./Version";
import { WhatWentWrong } from "./WhatWentWrong";

/**
 * The run screen: its header (how it came out, what it played, the ways to run it again), then
 * while it goes one progress card, and once it is done how many tests pass, what changed since an
 * earlier run, the results, and in a rail beside them what went wrong and the run's details.
 */
export function RunView({
  load,
  runs,
  label,
  scopeLabel,
  going,
  onBack,
  onBase,
  onTest,
  onRunTest,
  onRunMore,
  onRunOn,
  onStop,
  onAnalyze,
}: {
  load: Load<RunDetail>;
  runs: RunSummary[];
  label: (run: string) => string;
  scopeLabel: string;
  /** A run is going: run actions wait for it. */
  going: boolean;
  onBack: () => void;
  onBase: (run: string) => void;
  onTest: (id: string) => void;
  onRunTest: (row: CaseRow) => void;
  onRunMore: (ids: readonly string[], tries: number) => void;
  /** Opens the Run dialog for a set on another version: "Run them on main". */
  onRunOn?: (set: string, ref: string) => void;
  onStop: (run: string) => Promise<void>;
  onAnalyze: (run: string) => Promise<void>;
}) {
  if (load.status === "loading") return null;
  if (load.status !== "ready") {
    return <Centered>Couldn't load this run.</Centered>;
  }
  const run = load.data;
  const isLive = live(run);
  const found = tally(
    run.totals,
    run.cases.map((row) => row.verdict),
  );
  const failing = run.cases.some((row) => row.verdict === "fail" || row.verdict === "flaky");
  const reasons = new Map(run.tests.map((test) => [test.case, test.reason]));

  return (
    <Page wide>
      <RunHeader
        run={run}
        found={found}
        label={label}
        going={going}
        onBack={onBack}
        onRunMore={onRunMore}
        onRunOn={onRunOn}
        onStop={onStop}
      />
      <div className="grid items-start gap-4 min-[1100px]:grid-cols-[minmax(0,1fr)_340px]">
        <div className="flex min-w-0 flex-col gap-4">
          {isLive ? (
            <Progress run={run} onTest={onTest} />
          ) : (
            <>
              <ResultCard run={run} found={found} />
              <Comparison
                run={run}
                runs={runs}
                label={label}
                going={going}
                onTest={onTest}
                onRunMore={onRunMore}
                onRunOn={onRunOn ? (ref) => onRunOn(run.set, ref) : undefined}
              />
              <Card className="overflow-hidden">
                <Results
                  rows={run.cases}
                  going={going}
                  onTest={onTest}
                  onRun={onRunTest}
                  why={(id) => reasons.get(id) ?? null}
                />
              </Card>
              <Fold title="Scores and history">
                <Scores run={run} runs={runs} label={label} onBase={onBase} onTest={onTest} />
              </Fold>
            </>
          )}
        </div>
        <aside className="flex min-w-0 flex-col gap-4">
          {!isLive && failing ? (
            <WhatWentWrong run={run} onTest={onTest} onAnalyze={() => onAnalyze(run.run)} />
          ) : null}
          <RunFacts run={run} scopeLabel={scopeLabel} />
        </aside>
      </div>
    </Page>
  );
}

/** "Runs / <set>", "Run n" with how it came out, its facts as chips, and its actions. */
function RunHeader({
  run,
  found,
  label,
  going,
  onBack,
  onRunMore,
  onRunOn,
  onStop,
}: {
  run: RunDetail;
  found: Tally;
  label: (run: string) => string;
  going: boolean;
  onBack: () => void;
  onRunMore: (ids: readonly string[], tries: number) => void;
  onRunOn?: (set: string, ref: string) => void;
  onStop: (run: string) => Promise<void>;
}) {
  const state = runState(run, found);
  const isLive = live(run);
  const long = took(run);
  const copy = copyWords(run.isolation);
  const plays = playsWords(run.plays);
  // The tests that didn't pass, to run again with enough tries to tell a change from chance.
  const unsure = run.cases
    .filter((row) => row.verdict && row.verdict !== "pass")
    .map((row) => row.case);
  // Run again: the whole set on the version it played, or the same tests with the tries each had.
  const ref = run.version?.ref ?? "disk";
  const ids = run.cases.map((row) => row.case);
  const tries = Math.max(0, ...run.cases.map((row) => row.tries.length));
  const again =
    run.scope === "all"
      ? onRunOn
        ? () => onRunOn(run.set, ref)
        : null
      : ids.length && tries
        ? () => onRunMore(ids, tries)
        : null;
  return (
    <div className="flex flex-wrap items-start gap-5">
      <div className="min-w-0 flex-[1_1_420px]">
        <div className="mb-2 flex items-center gap-2 text-[13px] text-ink-3">
          <button
            type="button"
            onClick={onBack}
            className="text-tint-ink hover:underline focus-visible:outline-2 focus-visible:outline-tint"
          >
            Runs
          </button>
          <span aria-hidden>/</span>
          <span className="truncate">{run.set}</span>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="m-0 text-[30px] leading-[1.15] font-semibold tracking-[-0.01em] [overflow-wrap:anywhere] max-[560px]:text-[24px]">
            {label(run.run)}
          </h1>
          <Pill tone={state.tone}>{state.word}</Pill>
        </div>
        <div className="mt-3 flex flex-wrap gap-2" data-testid="run-chips">
          <Chip>{long ? `${when(run.started_at)} · ${long}` : when(run.started_at)}</Chip>
          <VersionChip version={run.version} />
          {copy ? <Chip>{copy}</Chip> : null}
          {run.environment.name ? <Chip>{run.environment.name}</Chip> : null}
          {plays ? <Chip tint>{plays}</Chip> : null}
        </div>
      </div>
      <div className="flex flex-wrap gap-2 pt-1">
        {run.status === "running" ? (
          <Button variant="secondary" onClick={() => void onStop(run.run)}>
            Stop
          </Button>
        ) : null}
        {!isLive && unsure.length ? (
          <Button
            variant="secondary"
            disabled={going}
            onClick={() => onRunMore(unsure, MORE_TRIES)}
            title={`Runs the ${count(unsure.length, "test")} that didn't pass, ${MORE_TRIES} tries each`}
          >
            {`Run with ${MORE_TRIES} tries`}
          </Button>
        ) : null}
        {!isLive && again ? (
          <Button disabled={going} onClick={again}>
            Run again
          </Button>
        ) : null}
      </div>
    </div>
  );
}

/** How many tests pass, as a ring, a sentence and a bar, with each kind counted; then, for a
 * run that ended early, why. */
function ResultCard({ run, found }: { run: RunDetail; found: Tally }) {
  const bar = parts(found);
  const why = run.stop_reason?.trim();
  const counted = [
    found.failed ? { tone: "fail", text: `${found.failed} failed every try` } : null,
    found.flaky ? { tone: "fail", text: `${found.flaky} failed some tries` } : null,
    found.unjudged ? { tone: "unjudged", text: `${found.unjudged} couldn't judge` } : null,
    found.notRun ? { tone: "none", text: `${found.notRun} not run` } : null,
  ].filter((entry) => entry !== null);
  const notices = [
    run.plays === "page_only" ? (
      <Notice key="page-only" tone="neutral" data-testid="page-only-run">
        {PAGE_ONLY_RUN}
      </Notice>
    ) : null,
    run.failure === "version_not_built" && why ? <NotBuilt key="not-built" reason={why} /> : null,
    run.failure !== "version_not_built" &&
    why &&
    (run.status === "failed" || run.status === "stopped") ? (
      <Notice
        key="why"
        tone={run.status === "failed" ? "fail" : "neutral"}
        data-testid="why-stopped"
      >
        <b className={run.status === "failed" ? "font-semibold text-fail" : "font-semibold"}>
          Why it stopped:
        </b>{" "}
        {why}
      </Notice>
    ) : null,
    run.interrupted ? (
      <Notice key="interrupted" tone="neutral">
        Interrupted: the server restarted under it, which says nothing of the app.
      </Notice>
    ) : null,
  ].filter(Boolean);
  return (
    <Card data-testid="run-result">
      <div className="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-6 p-5 max-[560px]:grid-cols-1">
        <span className="max-[560px]:hidden">
          <Ring
            parts={bar}
            total={found.total}
            label={`${found.passed} of ${found.total} passed`}
          />
        </span>
        <div className="min-w-0">
          <h2 className="m-0 text-[30px] leading-none font-semibold tracking-[-0.03em] max-[560px]:text-[24px]">
            {`${run.totals.passed} of ${count(run.totals.cases, "test")} pass`}
          </h2>
          <PartsBar parts={bar} total={found.total} className="mt-3 mb-2" />
          <p className="m-0 flex flex-wrap gap-x-5 gap-y-1 text-[13px] text-ink-2">
            {counted.map((entry) => (
              <span key={entry.text} className="inline-flex items-center gap-1.5">
                <Swatch tone={entry.tone as "fail" | "unjudged" | "none"} />
                {entry.text}
              </span>
            ))}
            {run.totals.passed_model_only ? (
              <span>{`${run.totals.passed_model_only} judged by a model only`}</span>
            ) : null}
            <span className="text-ink-3">{`${tokens(run.totals.tokens)} tokens`}</span>
          </p>
        </div>
      </div>
      {notices.length ? <div className="flex flex-col gap-2 px-5 pb-5">{notices}</div> : null}
    </Card>
  );
}

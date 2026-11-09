import { Square } from "lucide-react";
import { count, live, when } from "../../lib/format";
import { isolatedWords } from "../../lib/isolation";
import type { CaseRow, RunDetail, RunSummary } from "../../lib/schema";
import type { Load } from "../../lib/use-load";
import { PAGE_ONLY_RUN, verdictWord } from "../../lib/verdict";
import { Button } from "../../ui/button";
import { Fold, GroupHeading } from "../shared/group";
import { BackLink, Centered, Page, Toolbar, ToolbarTitle } from "../shared/layout";
import { Comparison } from "./Comparison";
import { Details } from "./Details";
import { Progress } from "./Progress";
import { Results } from "./Results";
import { CodeLines, NotBuilt } from "./Version";
import { WhatWentWrong } from "./WhatWentWrong";

/**
 * The run screen. While the run is going: one progress card. Once it is done: how many tests
 * pass, what went wrong (analyzed on its own when tests fail), the results, and the numbers
 * folded under Details.
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
  const failing = run.cases.some((row) => row.verdict === "fail" || row.verdict === "flaky");
  const failingCount = run.cases.filter((row) => row.verdict === "fail").length;
  const flakyCount = run.cases.filter((row) => row.verdict === "flaky").length;

  return (
    <>
      <Toolbar>
        <BackLink label="Runs" onClick={onBack} />
        <ToolbarTitle
          title={label(run.run)}
          sub={
            <>
              {scopeLabel} · {when(run.started_at)}
              {run.environment.name ? ` · ${run.environment.name}` : null}
              {run.isolation ? ` · ${isolatedWords(run.isolation)}` : null}
              {run.interrupted ? " · Interrupted" : null}
              {!run.interrupted && run.status === "stopped" ? " · Stopped" : null}
              {!run.interrupted && run.status === "failed" ? " · Didn't finish" : null}
            </>
          }
        />
        {run.status === "running" ? (
          <Button size="sm" variant="secondary" onClick={() => void onStop(run.run)}>
            <Square />
            Stop
          </Button>
        ) : null}
      </Toolbar>

      <Page>
        {isLive ? (
          <>
            <CodeLines version={run.version} />
            <Progress run={run} onTest={onTest} />
          </>
        ) : (
          <>
            <div className="flex flex-col gap-1">
              <h2 className="m-0 text-[28px] leading-[1.15] font-semibold tracking-[-0.5px]">
                {run.totals.passed} of {count(run.totals.cases, "test")} pass
              </h2>
              {failingCount || flakyCount || run.totals.unjudged || run.totals.passed_model_only ? (
                <p className="m-0 text-[14px] text-ink-2">
                  {[
                    failingCount ? `${failingCount} fail every try` : null,
                    flakyCount ? `${flakyCount} fail some tries` : null,
                    run.totals.unjudged
                      ? `${run.totals.unjudged} ${verdictWord("unjudged").toLowerCase()}`
                      : null,
                    run.totals.passed_model_only
                      ? `${run.totals.passed_model_only} judged by a model only`
                      : null,
                  ]
                    .filter(Boolean)
                    .join(", ")}
                </p>
              ) : null}
              {run.plays === "page_only" ? (
                <p className="m-0 text-[14px] text-ink-2" data-testid="page-only-run">
                  {PAGE_ONLY_RUN}
                </p>
              ) : null}
              <CodeLines version={run.version} className="mt-1" />
              {run.failure === "version_not_built" && run.stop_reason ? (
                <NotBuilt reason={run.stop_reason} />
              ) : null}
            </div>

            <Comparison
              run={run}
              runs={runs}
              label={label}
              going={going}
              onTest={onTest}
              onRunMore={onRunMore}
              onRunOn={onRunOn ? (ref) => onRunOn(run.set, ref) : undefined}
            />

            {failing ? (
              <WhatWentWrong run={run} onTest={onTest} onAnalyze={() => onAnalyze(run.run)} />
            ) : null}

            <section className="flex flex-col">
              <GroupHeading title="Results" />
              <Results rows={run.cases} going={going} onTest={onTest} onRun={onRunTest} />
            </section>

            <Fold title="Details">
              <Details run={run} runs={runs} label={label} onBase={onBase} onTest={onTest} />
            </Fold>
          </>
        )}
      </Page>
    </>
  );
}

import { useState } from "react";
import type { Scope } from "../../lib/api";
import { api } from "../../lib/api";
import { cn } from "../../lib/cn";
import { ago } from "../../lib/format";
import type { Period } from "../../lib/schema";
import { POLL_MS, useLoad, useVisiblePoll } from "../../lib/use-load";
import { Skeleton } from "../../ui/field";
import { LoadingState } from "../../ui/loading";
import { UiOffer } from "../environment/UiOffer";
import { RunAll } from "../runs/RunAll";
import { Centered } from "../shared/layout";
import { ChangeCard } from "./ChangeCard";
import { CARD } from "./card";
import { Dashboard } from "./Dashboard";
import { HowTestsRun } from "./HowTestsRun";
import { KnowledgeLine } from "./KnowledgeLine";
import { PageMapCard } from "./PageMapCard";
import { PERIODS } from "./period";
import { ReportCard } from "./ReportCard";

/**
 * Home: how the app is doing, in plain words. The latest run's report, four numbers,
 * what it's good at, passing over the last runs, what needs attention and the recent runs.
 */
export function HomeView({
  scope,
  lastRunAt,
  tests,
  stamp,
  going,
  runLabel,
  onRunAll,
  onRunSome,
  onWrite,
  onAsk,
  onGoing,
  onRun,
  onRuns,
  onTest,
  onReview,
}: {
  scope: Scope;
  /** When the newest run started, if there is one. */
  lastRunAt: number | null;
  tests: number;
  stamp: string;
  going: boolean;
  runLabel: (run: string) => string;
  onRunAll: () => void;
  onRunSome: (set: string, cases: readonly string[]) => void;
  /** Opens the test writer with these words ready to send. */
  onWrite: (text: string) => void;
  /** Sends these words to the test writer. */
  onAsk: (text: string, run?: string) => void;
  onGoing: () => void;
  onRun: (run: string) => void;
  onRuns: () => void;
  onTest: (id: string, run?: string) => void;
  onReview: () => void;
}) {
  const [period, setPeriod] = useState<Period>("7d");
  const [load, reload] = useLoad(`health:${scope.target}:${period}:${stamp}`, () =>
    api.health(scope.target, period),
  );
  const health = load.status === "ready" ? load.data : null;
  useVisiblePoll(reload, POLL_MS, health?.checking === true || health?.report_pending === true);
  const empty = !lastRunAt || health?.recent.length === 0;
  const runAll = tests ? <RunAll going={going} onGoing={onGoing} onRun={onRunAll} /> : null;
  const today = new Date().toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
  });

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <div className="mx-auto flex max-w-[1180px] flex-col gap-[22px] px-[clamp(16px,3vw,40px)] pt-7 pb-12">
        <header className="flex flex-wrap items-end gap-3.5">
          <div className="min-w-60 flex-1">
            <p className="m-0 text-[13px] text-ink-2">
              {today}
              {lastRunAt ? ` · last run ${ago(lastRunAt)}` : ""}
            </p>
            <h1 className="m-0 mt-1 text-[22px] leading-tight font-semibold tracking-[-0.02em]">
              How {scope.target} is doing
            </h1>
          </div>
          {empty ? null : (
            <fieldset
              aria-label="Period"
              className="m-0 flex gap-0.5 rounded-full border-0 bg-bubble p-[3px]"
            >
              {PERIODS.map(([id, label]) => (
                <button
                  key={id}
                  type="button"
                  aria-pressed={period === id}
                  onClick={() => setPeriod(id)}
                  className={cn(
                    "h-[30px] rounded-full px-3.5 text-[13px] font-medium focus-visible:outline-2 focus-visible:outline-ring",
                    period === id ? "bg-group text-foreground shadow-sm" : "text-ink-2",
                  )}
                >
                  {label}
                </button>
              ))}
            </fieldset>
          )}
          {empty ? null : runAll}
        </header>

        {empty ? (
          <>
            <div className={cn(CARD, "flex flex-col items-center gap-4 py-14 text-center")}>
              <p className="m-0 font-display text-[19px] font-bold">
                Run your tests to see how it's doing
              </p>
              {runAll}
            </div>
            {health ? <HowTestsRun target={scope.target} isolation={health.isolation} /> : null}
            {health ? (
              <PageMapCard target={scope.target} page={health.page} onAsk={(text) => onAsk(text)} />
            ) : null}
            <UiOffer target={scope.target} className={CARD} />
          </>
        ) : load.status === "loading" ? (
          <>
            <Skeleton className="h-48 rounded-xl" />
            <Skeleton className="h-72 rounded-xl" />
          </>
        ) : !health ? (
          <Centered>Couldn't load how it's doing.</Centered>
        ) : (
          <>
            {health.report_pending ? (
              <section className={CARD}>
                <LoadingState label="Writing the report" />
              </section>
            ) : health.report ? (
              <ReportCard
                report={health.report}
                runLabel={runLabel}
                onAsk={onAsk}
                onTest={onTest}
              />
            ) : null}
            <KnowledgeLine
              target={scope.target}
              knowledge={health.knowledge}
              onReview={onReview}
              onUpdated={() => void reload()}
            />
            <HowTestsRun target={scope.target} isolation={health.isolation} />
            <PageMapCard target={scope.target} page={health.page} onAsk={(text) => onAsk(text)} />
            <UiOffer target={scope.target} className={CARD} />
            {health.code_change ? (
              <ChangeCard
                change={health.code_change}
                canRead={health.knowledge.can_update}
                onRead={() => void reload()}
                scope={scope}
                going={going}
                onRun={onRunSome}
                onWrite={onWrite}
                onGoing={onGoing}
              />
            ) : null}
            <Dashboard
              health={health}
              runLabel={runLabel}
              onRun={onRun}
              onRuns={onRuns}
              onTest={onTest}
              onReview={onReview}
            />
          </>
        )}
      </div>
    </div>
  );
}

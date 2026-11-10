import { useMemo } from "react";
import type { Scope } from "../../lib/api";
import { api } from "../../lib/api";
import { cn } from "../../lib/cn";
import { runNumbers } from "../../lib/format";
import type { RunSummary } from "../../lib/schema";
import { POLL_MS, ready, useLoad, useVisiblePoll } from "../../lib/use-load";
import { Skeleton } from "../../ui/field";
import { LoadingState } from "../../ui/loading";
import { RunAll } from "../runs/RunAll";
import { Fold } from "../shared/group";
import { Areas } from "./Areas";
import { CARD } from "./card";
import { HowTestsRun } from "./HowTestsRun";
import { NeedsYou } from "./NeedsYou";
import { PassingCard, SpentCard, TypicalCard } from "./NumberCards";
import { usePageOffer } from "./OfferRow";
import { grid, passingCard, rankNeeds, settled, spentCard, typicalCard } from "./overview";
import { headerChips, PageHeader } from "./PageHeader";
import { RecentRuns } from "./RecentRuns";
import { ReportCard } from "./ReportCard";
import { RunGrid } from "./RunGrid";

/**
 * Overview (docs/mockups/design-studio.html, `home`): how the app is doing. The header and its
 * facts; what needs the person, ranked; three numbers with their run-by-run charts; each test run
 * by run beside what it's good at (the runs themselves on the Runs screen). Below: the latest run
 * in words, folded, and how tests run.
 */
export function HomeView({
  scope,
  lastRunAt,
  tests,
  stamp,
  going,
  runs: given,
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
  /** The set's runs, when the screen has them already; else Home reads them. */
  runs?: readonly RunSummary[];
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
  const [healthLoad, reloadHealth] = useLoad(`health:${scope.target}:${stamp}`, () =>
    api.health(scope.target, "7d"),
  );
  const health = ready(healthLoad);
  useVisiblePoll(
    reloadHealth,
    POLL_MS,
    health?.checking === true || health?.report_pending === true,
  );

  // The set's runs: the screen's when it passes them, else read here, and again while one plays.
  const [runsLoad, reloadRuns] = useLoad(
    given ? null : `home-runs:${scope.target}/${scope.set}:${stamp}`,
    () => api.runs(scope),
  );
  useVisiblePoll(reloadRuns, POLL_MS, !given && going);
  const runs = useMemo(
    () => [...(given ?? ready(runsLoad) ?? [])].sort((a, b) => b.started_at - a.started_at),
    [given, runsLoad],
  );
  const numbers = useMemo(() => runNumbers(runs), [runs]);
  const numbered = { runs, numbers };

  // The newest finished run's detail: the run it's compared with, and what got worse.
  const last = settled(runs);
  const [detailLoad] = useLoad(last ? `home-run:${last.run}` : null, () =>
    api.run(last?.run ?? ""),
  );
  const detail = ready(detailLoad);

  // The set as people name it: Health names each recent run's set.
  const setTitle =
    health?.recent.find((entry) => runs.some((each) => each.run === entry.run))?.name ?? null;

  const offer = usePageOffer(scope.target);
  const needs = rankNeeds({ ...numbered, detail, health, offer: offer.shows });

  const runAll = tests ? <RunAll going={going} onGoing={onGoing} onRun={onRunAll} /> : null;
  const header = (
    <PageHeader
      target={scope.target}
      lastRunAt={lastRunAt}
      chips={headerChips(runs[0] ?? null, health?.isolation ?? null)}
      runAll={runAll}
      onWrite={() => onWrite("")}
    />
  );
  const loadingRuns = !given && runsLoad.status === "loading";
  const plan = health?.isolation?.status;

  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <div className="mx-auto flex max-w-[1320px] flex-col gap-5 px-8 pt-7 pb-14 max-md:px-4 max-md:pt-5">
        {header}
        {loadingRuns || healthLoad.status === "loading" ? (
          <>
            <Skeleton className="h-40 rounded-xl" />
            <Skeleton className="h-72 rounded-xl" />
          </>
        ) : (
          <>
            <NeedsYou
              needs={needs}
              context={{
                scope,
                health,
                offer,
                going,
                onRun,
                onRunSome,
                onGoing,
                onTest,
                onReview,
                onWrite,
                onChanged: () => void reloadHealth(),
              }}
            />
            {runs.length ? (
              <>
                <Numbers numbered={numbered} detail={detail} />
                <div className="grid items-start gap-5 min-[1180px]:grid-cols-[minmax(0,1fr)_340px]">
                  <RunGrid
                    grid={grid(numbered)}
                    setName={setTitle}
                    onTest={(id, run) => onTest(id, run)}
                    onRun={onRun}
                    onRuns={onRuns}
                  />
                  {health?.areas.length ? <Areas areas={health.areas} /> : null}
                </div>
                <Fold title="Recent runs">
                  <RecentRuns runs={runs} numbers={numbers} onRun={onRun} onRuns={onRuns} />
                </Fold>
              </>
            ) : (
              <p className={cn(CARD, "m-0 text-sm text-ink-2")} data-testid="no-runs">
                No runs yet. Run your tests to see how it's doing.
              </p>
            )}
            {health?.report_pending ? (
              <section className={CARD}>
                <LoadingState label="Writing the report" />
              </section>
            ) : health?.report && runs.length ? (
              <Fold title="The latest run in words">
                <ReportCard
                  report={health.report}
                  runLabel={runLabel}
                  onAsk={onAsk}
                  onTest={onTest}
                />
              </Fold>
            ) : null}
            {health?.areas.length && !runs.length ? <Areas areas={health.areas} /> : null}
            {health?.isolation ? (
              <HowTestsRun
                target={scope.target}
                isolation={health.isolation}
                quiet={plan === "stale" || plan === "failed"}
              />
            ) : null}
            {healthLoad.status === "error" ? (
              <p className="m-0 text-[13px] text-ink-3">
                Couldn't load the rest of how it's doing.
              </p>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}

function Numbers({
  numbered,
  detail,
}: {
  numbered: Parameters<typeof spentCard>[0];
  detail: Parameters<typeof passingCard>[1];
}) {
  const passing = passingCard(numbered, detail);
  const typical = typicalCard(numbered);
  const spent = spentCard(numbered);
  if (!passing && !typical && !spent) return null;
  return (
    <div className="grid grid-cols-3 gap-5 max-lg:grid-cols-1">
      {passing ? <PassingCard card={passing} /> : null}
      {typical ? <TypicalCard card={typical} /> : null}
      {spent ? <SpentCard card={spent} /> : null}
    </div>
  );
}

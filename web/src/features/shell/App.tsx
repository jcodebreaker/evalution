import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { live, progressText } from "../../lib/format";
import type { Place } from "../../lib/router";
import { usePlace } from "../../lib/router";
import type { RunSummary } from "../../lib/schema";
import { ready, useLoad } from "../../lib/use-load";
import { isBenchmark } from "../benchmarks/BenchmarksView";
import { OpenEnvironment } from "../environment/use-isolation";
import type { RunTarget } from "../runs/RunConfirm";
import { RunConfirm } from "../runs/RunConfirm";
import { screenContext } from "../writer/context";
import { WriterPanel } from "../writer/WriterPanel";
import { AssistantButton } from "./AssistantButton";
import { backFor } from "./crumbs";
import { Screen } from "./Screen";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { followRun, useShown } from "./use-shown";
import { useWorkspace } from "./use-workspace";
import { useWriter } from "./use-writer";

/** The page: the sidebar, the screen, and the test writer from the end edge. */
export function App() {
  const [place, go] = usePlace();
  const [confirm, setConfirm] = useState<RunTarget | null>(null);
  const workspace = useWorkspace(place);
  const {
    targetList,
    target,
    setList,
    set,
    scope,
    scopeKey,
    reloadCases,
    reloadRuns,
    cards,
    rows,
    runs,
    runLabel,
    testName,
    going,
    busy,
    goingBenchmark,
    reloadBenchmarkRuns,
  } = workspace;
  // The isolation plan, for the sidebar's foot: read again on each screen, so proving it shows.
  const [planLoad, reloadPlan] = useLoad(target ? `isolation:${target}` : null, () =>
    api.isolation(target ?? ""),
  );
  const view = place.view;
  // biome-ignore lint/correctness/useExhaustiveDependencies: read again when the screen changes
  useEffect(() => {
    void reloadPlan();
  }, [view, reloadPlan]);
  const writer = useWriter(scope, reloadRuns, reloadCases);
  const { agent, ask, writerOpen, setWriterOpen, writerDraft, clearDraft, mode, openAssistant } =
    writer;
  const shown = useShown(place, scope, scopeKey, rows, runs, writer.agentMessages);
  // A run opened from another set's page (Home, a link) takes the page to its own set.
  const follow = followRun(place, ready(shown.detail), { target, set });
  const followTarget = follow?.target;
  const followSet = follow?.set;
  useEffect(() => {
    if (followSet) go({ target: followTarget, set: followSet }, true);
  }, [followTarget, followSet, go]);

  const runTest = (id: string, name: string) => {
    if (scope && !busy) setConfirm({ scope, test: { id, name } });
  };
  const runAll = () => {
    if (scope && !busy) setConfirm({ scope });
  };
  const runMore = (ids: readonly string[], tries: number) => {
    if (scope && !busy) setConfirm({ scope, cases: ids, tries });
  };
  const runSome = (inSet: string, ids: readonly string[]) => {
    if (target && !busy) setConfirm({ scope: { target, set: inSet }, cases: ids });
  };
  const runSet = (inSet: string) => {
    if (target) setConfirm({ scope: { target, set: inSet } });
  };
  const runOn = (inSet: string, ref: string) => {
    if (target && !busy) setConfirm({ scope: { target, set: inSet }, ref });
  };
  const showRun = (id: string) => go({ view: "runs", run: id, case: null, base: null });
  const showBenchmark = (id: string) => go({ view: "benchmarks", run: id, case: null, base: null });
  const showGoing = () => {
    if (going) showRun(going.run);
  };
  const started = (summary: RunSummary) => {
    const benchmark = confirm ? isBenchmark(confirm.scope.set) : false;
    setConfirm(null);
    void reloadRuns();
    if (benchmark) {
      void reloadBenchmarkRuns();
      go({ view: "benchmarks", run: summary.run, case: null, base: null });
    } else showRun(summary.run);
  };
  const nav = (next: Partial<Place>) =>
    go({
      run: null,
      case: null,
      base: null,
      ...(place.set && isBenchmark(place.set) ? { set: null } : {}),
      ...next,
    });
  const openTest = (id: string, inRun?: string) =>
    go({ view: "tests", case: id, run: inRun ?? null, base: null });
  const learnAgain = () => {
    if (!target) return;
    const changed = cards?.get(target)?.status === "out_of_date";
    void ask(
      `Learn ${target} again from its recent traces and its code.${changed ? " Then update the tests that may be stale." : ""}`,
      undefined,
      true,
    );
  };

  /** The Test environment screen, from Home, the Run dialog or the connection page. */
  const openEnvironment = () => {
    setConfirm(null);
    nav({ view: "environment" });
  };

  const playing = going ?? goingBenchmark;
  const playingLabel = going ? runLabel(going.run) : "Benchmark";
  const lastDone = runs.find((run) => !live(run));
  const failing = lastDone?.tests.filter((entry) => entry.verdict === "fail").length ?? 0;
  const benchmarkRun = (id: string) => workspace.benchmarkRuns.some((entry) => entry.run === id);
  const back = backFor(place, { runLabel, benchmarkRun });
  const context =
    mode === "screen"
      ? screenContext(place, { testName, runLabel, testRun: shown.testRun, benchmarkRun })
      : null;

  const panel = writerOpen && scope !== null;
  return (
    <OpenEnvironment.Provider value={target ? openEnvironment : null}>
      <div
        className={
          panel
            ? "canvas grid h-full text-foreground max-md:h-auto max-md:grid-cols-[minmax(0,1fr)] max-md:min-h-full md:grid-cols-[244px_minmax(0,1fr)_380px]"
            : "canvas grid h-full text-foreground max-md:h-auto max-md:grid-cols-[minmax(0,1fr)] max-md:min-h-full md:grid-cols-[244px_minmax(0,1fr)]"
        }
      >
        <Sidebar
          place={place}
          targets={targetList}
          target={target}
          sets={setList}
          set={set}
          tests={scope && workspace.cases.status === "ready" ? rows.length : null}
          failing={failing}
          plan={ready(planLoad)}
          cards={cards}
          onTarget={(name) => nav({ target: name, set: null, view: "home" })}
          onSet={(id) => nav({ set: id })}
          onView={(next) => nav({ view: next })}
        />
        <main className="flex min-h-0 min-w-0 flex-col max-md:min-h-[70vh]">
          <TopBar
            back={
              back
                ? {
                    label: back.label,
                    onClick: () => {
                      if (back.list) nav({ view: back.list });
                      else go(back.to);
                    },
                  }
                : null
            }
            live={
              playing
                ? {
                    label: `${playingLabel} · ${
                      playing.analyzing
                        ? "Analyzing"
                        : progressText(playing, going ? "tests" : "questions")
                    }`,
                    onOpen: () => {
                      if (going) showGoing();
                      else if (goingBenchmark) showBenchmark(goingBenchmark.run);
                    },
                  }
                : null
            }
          />
          <Screen
            place={place}
            go={go}
            nav={nav}
            workspace={workspace}
            writer={writer}
            shown={shown}
            runTest={runTest}
            runMore={runMore}
            runAll={runAll}
            runSome={runSome}
            runSet={runSet}
            runOn={runOn}
            showRun={showRun}
            showGoing={showGoing}
            openTest={openTest}
            learnAgain={learnAgain}
          />
        </main>
        {panel ? (
          <WriterPanel
            target={scope?.target ?? null}
            agent={agent}
            runs={runs}
            runLabel={runLabel}
            testName={testName}
            going={going !== null}
            onClose={() => setWriterOpen(false)}
            onRun={showRun}
            onRunTest={(id) => runTest(id, testName(id))}
            onTest={openTest}
            onLearned={() => nav({ view: "app" })}
            draft={writerDraft}
            onDrafted={clearDraft}
            context={context}
          />
        ) : scope ? (
          <AssistantButton onClick={openAssistant} />
        ) : null}

        {confirm ? (
          <RunConfirm target={confirm} onClose={() => setConfirm(null)} onStarted={started} />
        ) : null}
      </div>
    </OpenEnvironment.Provider>
  );
}

import { Bot } from "lucide-react";
import { useState } from "react";
import type { Place } from "../../lib/router";
import { usePlace } from "../../lib/router";
import type { RunSummary } from "../../lib/schema";
import { Button } from "../../ui/button";
import { Spinner } from "../../ui/loading";
import { isBenchmark } from "../benchmarks/BenchmarksView";
import type { RunTarget } from "../runs/RunConfirm";
import { RunConfirm } from "../runs/RunConfirm";
import { WriterPanel } from "../writer/WriterPanel";
import { Screen } from "./Screen";
import { Sidebar } from "./Sidebar";
import { useShown } from "./use-shown";
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
    goingBenchmark,
    reloadBenchmarkRuns,
  } = workspace;
  const writer = useWriter(scope, reloadRuns, reloadCases);
  const { agent, ask, writerOpen, setWriterOpen, writerDraft, clearDraft } = writer;
  const shown = useShown(place, scope, scopeKey, rows, runs, writer.agentMessages);

  const runTest = (id: string, name: string) => {
    if (scope && !going) setConfirm({ scope, test: { id, name } });
  };
  const runAll = () => {
    if (scope && !going) setConfirm({ scope });
  };
  const runMore = (ids: readonly string[], tries: number) => {
    if (scope && !going) setConfirm({ scope, cases: ids, tries });
  };
  const runSome = (inSet: string, ids: readonly string[]) => {
    if (target && !going) setConfirm({ scope: { target, set: inSet }, cases: ids });
  };
  const runSet = (inSet: string) => {
    if (target) setConfirm({ scope: { target, set: inSet } });
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
    );
  };

  const panel = writerOpen && scope !== null;
  const working = agent.session?.status === "working";
  return (
    <div
      className={
        panel
          ? "grid h-full bg-window text-foreground max-md:h-auto max-md:grid-cols-[minmax(0,1fr)] max-md:min-h-full md:grid-cols-[240px_minmax(0,1fr)_380px]"
          : "grid h-full bg-window text-foreground max-md:h-auto max-md:grid-cols-[minmax(0,1fr)] max-md:min-h-full md:grid-cols-[240px_minmax(0,1fr)]"
      }
    >
      <Sidebar
        place={place}
        targets={targetList}
        target={target}
        sets={setList}
        set={set}
        going={going ?? goingBenchmark}
        goingLabel={going ? runLabel(going.run) : "Benchmark"}
        cards={cards}
        onTarget={(name) => nav({ target: name, set: null, view: "home" })}
        onSet={(id) => nav({ set: id })}
        onView={(view) => nav({ view })}
        onGoing={() => {
          if (going) showGoing();
          else if (goingBenchmark) showBenchmark(goingBenchmark.run);
        }}
      />
      <main className="flex min-h-0 min-w-0 flex-col bg-content max-md:min-h-[70vh]">
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
          showRun={showRun}
          showGoing={showGoing}
          openTest={openTest}
          learnAgain={learnAgain}
        />
      </main>
      {panel ? (
        <WriterPanel
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
        />
      ) : agent.session && scope ? (
        <Button
          variant="secondary"
          className="fixed end-4 bottom-4 z-30 h-9 rounded-full bg-group px-3.5 shadow-lg ring-1 ring-separator"
          onClick={() => setWriterOpen(true)}
        >
          {working ? <Spinner /> : <Bot />}
          Test writer
        </Button>
      ) : null}

      {confirm ? (
        <RunConfirm target={confirm} onClose={() => setConfirm(null)} onStarted={started} />
      ) : null}
    </div>
  );
}

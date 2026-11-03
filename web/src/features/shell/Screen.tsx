import { api } from "../../lib/api";
import { live } from "../../lib/format";
import type { Place } from "../../lib/router";
import type { CaseRow, PassMarks } from "../../lib/schema";
import { Button } from "../../ui/button";
import { Skeleton } from "../../ui/field";
import { ConnectView } from "../connect/ConnectView";
import { HomeView } from "../home/HomeView";
import { AppView } from "../learned/AppView";
import { RunAll } from "../runs/RunAll";
import { RunsView } from "../runs/RunsView";
import { RunView } from "../runs/RunView";
import { ModelSettingsView } from "../settings/ModelSettingsView";
import { Centered } from "../shared/layout";
import { TestView } from "../test/TestView";
import { TestsView } from "../tests/TestsView";
import type { Shown } from "./use-shown";
import type { Workspace } from "./use-workspace";
import type { Writer } from "./use-writer";

const DEFAULT_PASS_MARKS: PassMarks = { tools: 0.8, reply: 0.7 };

/** The screen `place` asks for, once what it needs has loaded. */
export function Screen({
  place,
  go,
  nav,
  workspace,
  writer,
  shown,
  runTest,
  runMore,
  runAll,
  runSome,
  showRun,
  showGoing,
  openTest,
  learnAgain,
}: {
  place: Place;
  go: (next: Partial<Place>) => void;
  nav: (next: Partial<Place>) => void;
  workspace: Workspace;
  writer: Writer;
  shown: Shown;
  runTest: (id: string, name: string) => void;
  runMore: (ids: readonly string[], tries: number) => void;
  runAll: () => void;
  runSome: (inSet: string, ids: readonly string[]) => void;
  showRun: (id: string) => void;
  showGoing: () => void;
  openTest: (id: string, inRun?: string) => void;
  learnAgain: () => void;
}) {
  const {
    targets,
    reloadTargets,
    target,
    sets,
    reloadSets,
    setList,
    set,
    scope,
    cases,
    reloadCases,
    runsLoad,
    reloadRuns,
    rows,
    runs,
    runLabel,
    scopeLabel,
    going,
  } = workspace;
  const { agent, ask, setLearn, setWriterOpen, setWriterDraft } = writer;
  const { row, testRun, detail, reloadDetail, file, reloadFile, testLive } = shown;

  const setSummary = setList.find((entry) => entry.set === set) ?? null;
  const passMarks =
    setSummary?.pass_marks ?? runs.find((run) => !live(run))?.pass_marks ?? DEFAULT_PASS_MARKS;

  if (place.view === "model") {
    return <ModelSettingsView target={target} />;
  }
  if (targets.status === "error" || sets.status === "error") {
    return <LoadError />;
  }
  if (place.view === "connect" || (targets.status === "ready" && !target)) {
    return (
      <ConnectView
        first={!target}
        onReady={async (name) => {
          // Connected: the page follows it, and the rest of connecting stays on this screen.
          go({ view: "connect" });
          await reloadTargets();
          nav({ target: name, set: null, view: "connect" });
        }}
        onLearn={setLearn}
        onLearned={() => nav({ view: "app" })}
        onAsk={ask}
      />
    );
  }
  if (targets.status === "loading" || (target && sets.status === "loading")) {
    return <MainSkeleton />;
  }
  if (!target) {
    return null;
  }
  if (place.view === "app") {
    return (
      <AppView
        target={target}
        agent={agent.session}
        onLearn={learnAgain}
        onAdd={async (starters) => {
          const open = agent.session?.status === "stopped" ? undefined : agent.session?.id;
          const session = await api.plan(target, starters, {
            session: open,
            set: set ?? undefined,
          });
          if (session) agent.adopt(session);
          else await agent.refresh();
          setWriterOpen(true);
        }}
        onSaved={reloadTargets}
        onRemoved={() => {
          void reloadTargets();
          nav({ target: null, set: null, view: "home" });
        }}
      />
    );
  }
  if (!scope) {
    return <Centered>No tests yet</Centered>;
  }
  if (cases.status === "loading" || runsLoad.status === "loading") {
    return <MainSkeleton />;
  }
  if (place.view === "home") {
    return (
      <HomeView
        scope={scope}
        lastRunAt={runs[0]?.started_at ?? null}
        tests={rows.length}
        stamp={runs[0]?.run ?? ""}
        going={going !== null}
        runLabel={runLabel}
        onRunAll={runAll}
        onRunSome={runSome}
        onWrite={(text) => {
          setWriterDraft(text);
          setWriterOpen(true);
        }}
        onAsk={(text, run) => void ask(text, run)}
        onGoing={showGoing}
        onRun={showRun}
        onRuns={() => nav({ view: "runs" })}
        onTest={openTest}
        onReview={() => nav({ view: "app" })}
      />
    );
  }
  if (place.case) {
    const fromRun = place.view === "runs" && place.run;
    return (
      <TestView
        scope={scope}
        appName={target}
        id={place.case}
        file={file}
        run={testRun}
        live={testLive}
        runLabel={runLabel}
        stale={row?.stale ?? null}
        going={going !== null}
        back={
          fromRun
            ? { label: runLabel(place.run ?? ""), onClick: () => go({ case: null }) }
            : { label: "Tests", onClick: () => nav({ view: "tests" }) }
        }
        onRun={(name) => runTest(place.case ?? "", name)}
        onRunResult={(id) => go({ run: id })}
        onAsk={(text, run) => void ask(text, run)}
        onEdited={() => {
          void reloadFile();
          void reloadCases();
        }}
      />
    );
  }
  if (place.view === "runs" && place.run) {
    const summary = runs.find((run) => run.run === place.run);
    return (
      <RunView
        load={detail}
        runs={runs}
        label={runLabel}
        scopeLabel={summary ? scopeLabel(summary) : ""}
        going={going !== null}
        onBack={() => nav({ view: "runs" })}
        onBase={(id) => go({ base: id })}
        onTest={(id) => go({ case: id })}
        onRunTest={(entry: CaseRow) => runTest(entry.case, entry.name)}
        onRunMore={runMore}
        onStop={async (id) => {
          await api.stop(id);
          await reloadDetail();
          await reloadRuns();
        }}
        onAnalyze={async (id) => {
          await api.analyze(id);
          await reloadDetail();
          await reloadRuns();
        }}
      />
    );
  }
  if (place.view === "runs") {
    return (
      <RunsView
        runs={runs}
        label={runLabel}
        onOpen={(id, test) => go({ run: id, case: test })}
        empty={
          rows.length ? (
            <RunAll
              scope={scope}
              tests={rows.length}
              stamp=""
              going={going !== null}
              onGoing={showGoing}
              onRun={runAll}
            />
          ) : null
        }
      />
    );
  }
  return (
    <TestsView
      scope={scope}
      rows={rows}
      passMarks={passMarks}
      stamp={runs[0]?.run ?? ""}
      going={going !== null}
      onAsk={ask}
      onPassMarks={reloadSets}
      onGoing={showGoing}
      onRunAll={runAll}
      onRun={(entry) => runTest(entry.case, entry.name)}
      onOpen={(entry) => openTest(entry.case)}
    />
  );
}

function LoadError() {
  return (
    <Centered>
      <p className="m-0">Couldn't reach the server.</p>
      <Button variant="secondary" onClick={() => window.location.reload()}>
        Retry
      </Button>
    </Centered>
  );
}

function MainSkeleton() {
  return (
    <div className="mx-auto flex w-full max-w-[860px] flex-col gap-6 px-6 pt-16">
      <Skeleton className="h-20 rounded-xl" />
      <Skeleton className="h-64 rounded-xl" />
    </div>
  );
}

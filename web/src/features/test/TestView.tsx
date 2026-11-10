import { ArrowRight, ExternalLink, Play, Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Scope } from "../../lib/api";
import { api } from "../../lib/api";
import { cn } from "../../lib/cn";
import { seconds, tokens, when } from "../../lib/format";
import type { CaseFileView, Compare, Finding, TryResult } from "../../lib/schema";
import type { Load } from "../../lib/use-load";
import { POLL_MS, ready, useLoad, useVisiblePoll } from "../../lib/use-load";
import { verdictWord } from "../../lib/verdict";
import { Button, buttonClass } from "../../ui/button";
import { Fold } from "../shared/group";
import { Chip, Dot, PageHead, Panel, PanelHead, Seg, Tabs, TD, TH } from "../shared/kit";
import { Centered, Page } from "../shared/layout";
import { StaleChip } from "../shared/marks";
import { BrokenMapNote } from "../shared/next-step";
import { Analysis } from "./Analysis";
import { Earlier } from "./Earlier";
import { EditFile } from "./EditFile";
import type { Doubt } from "./HowDecided";
import { HowDecided } from "./HowDecided";
import { HowItPlays } from "./HowItPlays";
import { SpanInspector, StepInspector } from "./Inspector";
import { PageNextStep } from "./NextStep";
import { oneList, rowOfSpan, rowsOf, rubricLine } from "./one-list";
import { PageNotes } from "./Page";
import { PageFilm } from "./PageFilm";
import { StepList } from "./StepList";
import { Steps } from "./Steps";
import { Timeline } from "./Timeline";
import { Trajectory } from "./Trajectory";
import { ModelSaw, RawSteps } from "./TryContext";
import { Turns } from "./Turns";
import { type Fact, playsFact, tryFacts } from "./try-words";
import { uiSentences } from "./ui-checks";
import { VerdictBanner } from "./VerdictBanner";

/** The rubric lines that failed on the try shown although the grader is unreliable on them. */
function doubtsOf(shown: Compare): Doubt[] {
  const lines = shown.steps.flatMap((step) =>
    step.items.filter((item) => item.id.startsWith("rubric-")),
  );
  return lines.flatMap((item, index) => {
    const said = shown.agreement[item.id];
    const failed = item.actual && !item.actual.passed && !item.actual.unjudged;
    return failed && said?.unreliable
      ? [{ line: index + 1, disagreed: said.labelled - said.agreed, labelled: said.labelled }]
      : [];
  });
}

export type TestTab = "result" | "plan" | "history";
export type Work = "steps" | "trace" | "model" | "decided";

/**
 * One test: its heading once (the top bar holds the way back), its facts, and three tabs. The
 * latest result: the verdict with its tries and layers, the analysis with evidence that jumps to
 * its step, a workbench (step by step with an inspector beside it, the trace, what the model saw,
 * how it was decided), what the page showed, and earlier results. What it checks, step by step,
 * with how it plays. Its history, run by run. Its file can be edited by hand.
 */
export function TestView({
  scope,
  appName,
  id,
  file,
  run,
  live,
  runLabel,
  stale,
  going,
  onRun,
  onRunResult,
  onAsk,
  onEdited,
}: {
  scope: Scope;
  /** What the tested app is called in the steps. */
  appName: string;
  id: string;
  file: Load<CaseFileView>;
  /** The run whose result is shown; `null` when the test has never run. */
  run: string | null;
  /** That run is still going. */
  live: boolean;
  runLabel: (run: string) => string;
  stale: string | null;
  going: boolean;
  /** The way back: the shell's top bar draws it, so the screen doesn't. */
  back?: { label: string; onClick: () => void };
  onRun: (name: string) => void;
  /** Shows this test's result in another run. */
  onRunResult: (run: string) => void;
  onAsk: (text: string, run?: string) => void;
  /** The file was saved: read the test again. */
  onEdited: () => void;
}) {
  const test = ready(file);
  // The plan follows the file: an edited test is worded again.
  const fingerprint = test ? JSON.stringify([test.turns, test.tools, test.reply]) : null;
  const [plan, reloadPlan] = useLoad(
    fingerprint ? `plan:${scope.target}/${scope.set}/${id}:${fingerprint}` : null,
    () => api.testPlan(scope, id),
  );
  const [tryN, setTryN] = useState<number | null>(null);
  const [tab, setTab] = useState<TestTab | null>(null);
  const [work, setWork] = useState<Work>("steps");
  const [picked, setPicked] = useState<number | null>(null);
  // A UI check picked, whose screenshot the inspector shows while its row is the one picked.
  const [check, setCheck] = useState<{ row: number; key: string } | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const key = `${id}:${run}`;
  // A try asked for in another run, shown once that run's result is, at the step asked for.
  const jump = useRef<{ key: string; n: number; span: string | null } | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  useEffect(() => {
    const to = jump.current?.key === key ? jump.current : null;
    jump.current = null;
    setTryN(to?.n ?? null);
    setTab(null);
    setPicked(null);
    setFocus(to?.span ?? null);
    setPending(to?.span ?? null);
  }, [key]);
  const [result, reloadResult] = useLoad(run ? `compare:${run}:${id}:${tryN ?? ""}` : null, () =>
    api.tryResult(run ?? "", id, tryN),
  );
  useVisiblePoll(reloadResult, POLL_MS, live || !!ready(result)?.wording);
  useVisiblePoll(reloadPlan, POLL_MS, !!ready(plan)?.wording);
  const failed = ready(result)?.verdict;
  const [finding] = useLoad(
    run && !live && (failed === "fail" || failed === "flaky") ? `finding:${run}:${id}` : null,
    () => api.finding(run ?? "", id),
  );
  const shown: TryResult | null = ready(result);
  const list = useMemo(() => (shown ? oneList(shown) : null), [shown]);
  const rows = useMemo(() => (list ? rowsOf(list.entries) : []), [list]);

  /** Shows the step that holds `span` in "Step by step", else the span on the trace. */
  const jumpTo = (span: string | null) => {
    const index = span
      ? rowOfSpan(rows, span)
      : rows.findLastIndex((row) => row.kind === "reply" || row.kind === "calls");
    if (index >= 0) {
      setWork("steps");
      setPicked(index);
      requestAnimationFrame(() =>
        document
          .getElementById(`step-row-${index}`)
          ?.scrollIntoView?.({ block: "center", behavior: "smooth" }),
      );
    } else if (span && shown?.detail?.timeline.some((entry) => entry.id === span)) {
      setWork("trace");
      setFocus(span);
    }
  };
  // A passing try opened at the step where they differ: once it's shown, go to that step.
  // biome-ignore lint/correctness/useExhaustiveDependencies: once the try asked for is shown
  useEffect(() => {
    if (pending && shown && shown.try === tryN) {
      setPending(null);
      jumpTo(pending);
    }
  }, [pending, shown, tryN]);

  if (file.status === "loading") return null;
  if (!test) return <Centered>Couldn't load this test.</Centered>;
  const hasResult = !!shown && shown.tries.length > 0;
  const view: TestTab = tab ?? (hasResult ? "result" : "plan");
  const head = (view === "result" ? shown : null) ?? ready(plan);
  // Limits in words, not where the test came from.
  const limits = (head?.limits ?? []).filter((limit) => !/^from\b/i.test(limit));
  const tryShown = view === "result" && shown?.try ? shown : null;
  // "Try 3 of 3": the tries a run kept may skip a number.
  const tries = shown ? Math.max(shown.tries.length, ...shown.tries.map((entry) => entry.n)) : 0;
  const facts: Fact[] = tryShown
    ? [
        ...tryFacts(tryShown),
        { text: `${runLabel(tryShown.run)} · try ${tryShown.try} of ${tries}` },
      ]
    : [playsFact(test.plays), ...limits.map((text) => ({ text }))];

  const diverged =
    shown?.divergence && shown.divergence.fail.run === shown.run ? shown.divergence : null;
  const mine = diverged && diverged.fail.n === shown?.try ? diverged : null;
  const differs = mine ? rowOfSpan(rows, mine.fail.span ?? "") : -1;
  const openPassing = (other: string, n: number, at: string | null) => {
    if (other === run) {
      setTryN(n);
      setFocus(at);
      setPending(at);
    } else {
      jump.current = { key: `${id}:${other}`, n, span: at };
      onRunResult(other);
    }
  };
  const failedRow = rows.findIndex((row) => row.status === "failed");
  const selected = picked ?? (rows.length ? Math.max(failedRow, 0) : null);
  const selectedRow = selected === null ? null : rows[selected];
  const pickedCheck = check && check.row === selected ? check.key : null;
  const spans = shown?.detail?.timeline ?? [];
  const span =
    spans.find((entry) => entry.id === focus && entry.kind !== "missing") ??
    spans.find((entry) => entry.check && entry.kind !== "missing") ??
    spans.find((entry) => entry.kind !== "missing") ??
    null;
  const traceTokens = spans.reduce(
    (total, entry) => total + (entry.tokens_in ?? 0) + (entry.tokens_out ?? 0),
    0,
  );
  const traceSeconds = Math.max(0, ...spans.map((entry) => entry.start + entry.seconds));
  const hasTrace = spans.length > 0 || !!shown?.trajectory || !!shown?.detail?.trajectory.length;
  const hasModel = !!shown?.detail?.context.length;
  const ask = (text: string) => onAsk(text, shown?.run);

  return (
    <>
      <Page full>
        <PageHead
          title={test.name}
          sub={test.about}
          chips={
            facts.length || stale ? (
              <>
                {facts.map((fact) => (
                  <Chip key={fact.text} tone={fact.tint ? "tint" : "plain"}>
                    {fact.text}
                  </Chip>
                ))}
                {stale ? <StaleChip reason={stale} /> : null}
              </>
            ) : null
          }
          actions={
            <>
              <Button
                variant="ghost"
                size="lg"
                onClick={() => onAsk(`Write variations of "${test.name}".`)}
              >
                Variations
              </Button>
              <Button variant="secondary" size="lg" onClick={() => setEditing(true)}>
                Edit test
              </Button>
              <Button size="lg" disabled={going} onClick={() => onRun(test.name)}>
                <Play />
                {hasResult ? "Run this test again" : "Run this test"}
              </Button>
            </>
          }
        />
        <Tabs
          label="This test"
          value={view}
          onChange={setTab}
          options={[
            {
              value: "result",
              label: hasResult ? "Latest result" : live ? "Running…" : "Not run yet",
              disabled: !hasResult,
            },
            { value: "plan", label: "What it checks" },
            { value: "history", label: "History", count: test.history.length },
          ]}
        />

        {view === "result" && shown ? (
          <>
            <VerdictBanner
              shown={shown}
              runLabel={runLabel}
              onTry={(n) => {
                setTryN(n);
                setPicked(null);
                setFocus(null);
              }}
            />
            {shown.page?.broken_map ? (
              <BrokenMapNote
                target={scope.target}
                missing={[shown.page.broken_map.missing]}
                shot={shown.page.broken_map.shot_url}
                onRelearn={(text) => onAsk(text)}
              />
            ) : null}
            {tryShown ? (
              <PageNextStep
                shown={tryShown}
                appName={appName}
                going={going}
                onRun={() => onRun(test.name)}
              />
            ) : null}
            <Analysis
              finding={ready(finding) as Finding | null}
              divergence={mine}
              entries={list?.entries ?? []}
              passLabel={
                mine
                  ? `try ${mine.pass.n}${mine.pass.run === shown.run ? "" : ` of ${runLabel(mine.pass.run)}`}`
                  : ""
              }
              stepOf={(at) => {
                const index = at ? rowOfSpan(rows, at) : -1;
                return index >= 0 ? index + 1 : null;
              }}
              onJump={jumpTo}
              onPassing={() => {
                if (mine) openPassing(mine.pass.run, mine.pass.n, mine.pass.span);
              }}
            />
            {shown.trace_note ? <p className="m-0 text-sm text-unj">{shown.trace_note}</p> : null}

            {shown.try ? (
              <>
                <div className="flex flex-wrap items-center gap-2.5">
                  <Seg
                    label="Workbench"
                    value={work}
                    onChange={setWork}
                    options={[
                      { value: "steps", label: "Step by step" },
                      { value: "trace", label: "Trace", disabled: !hasTrace },
                      { value: "model", label: "What the model saw", disabled: !hasModel },
                      { value: "decided", label: "How it was decided", disabled: !shown.explain },
                    ]}
                  />
                  <span className="flex-1" />
                  {shown.outcome === "failed" ? (
                    <Button
                      variant="ghost"
                      aria-label="Why did it fail?"
                      onClick={() =>
                        ask(
                          `Why did "${test.name}" fail on try ${shown.try} of ${runLabel(shown.run)}?`,
                        )
                      }
                    >
                      <Sparkles />
                      Why?
                    </Button>
                  ) : null}
                  {shown.trace_url ? (
                    <a
                      href={shown.trace_url}
                      target="_blank"
                      rel="noreferrer"
                      className={buttonClass("ghost")}
                    >
                      <ExternalLink />
                      Open the trace
                    </a>
                  ) : null}
                </div>

                {work === "steps" ? (
                  <div className="grid grid-cols-[minmax(0,1fr)_380px] items-start gap-5 max-xl:grid-cols-1">
                    <Panel aria-label="Step by step" data-from={list?.from}>
                      <StepList
                        rows={rows}
                        appName={appName}
                        selected={selected}
                        onSelect={setPicked}
                        isolation={shown.isolation}
                        agreement={shown.agreement}
                        highlights={shown.output?.highlights}
                        differs={differs >= 0 ? differs : null}
                        pickedCheck={pickedCheck}
                        onPickCheck={(index, key) => {
                          setPicked(index);
                          setCheck({ row: index, key });
                        }}
                        onLabel={(line, agree) => {
                          // A rubric line by its place; a look by its check's id (`look:0`).
                          const n = /^look:\d+$/.test(line) ? line : rubricLine(line);
                          if (n === null || !shown.try) return;
                          void api
                            .label(shown.run, id, shown.try, n, agree)
                            .then(reloadResult, () => null);
                        }}
                      />
                    </Panel>
                    {selectedRow && selected !== null ? (
                      <StepInspector
                        row={selectedRow}
                        index={selected}
                        shown={shown}
                        appName={appName}
                        onAsk={ask}
                        check={pickedCheck}
                      />
                    ) : null}
                  </div>
                ) : null}

                {work === "trace" ? (
                  <div className="flex flex-col gap-4">
                    <div className="grid grid-cols-[minmax(0,1fr)_380px] items-start gap-5 max-xl:grid-cols-1">
                      <Panel aria-label="Trace">
                        <PanelHead title="Trace">
                          <span className="flex-1" />
                          {spans.length ? (
                            <span className="font-mono text-[13px] text-ink-3">
                              {[
                                `${spans.length} spans`,
                                seconds(traceSeconds, true),
                                traceTokens ? `${tokens(traceTokens)} tokens` : null,
                              ]
                                .filter(Boolean)
                                .join(" · ")}
                            </span>
                          ) : null}
                        </PanelHead>
                        <div className="p-3">
                          {spans.length ? (
                            <Timeline
                              spans={spans}
                              selected={span?.id ?? null}
                              onSelect={setFocus}
                            />
                          ) : (
                            <p className="m-0 p-3 text-ink-3">No spans kept</p>
                          )}
                        </div>
                      </Panel>
                      {span ? (
                        <SpanInspector
                          span={span}
                          onAsk={() =>
                            ask(`Why did ${appName} call ${span.name} on try ${shown.try}?`)
                          }
                        />
                      ) : null}
                    </div>
                    {shown.trajectory ? (
                      <Fold title="Each try's route">
                        <Trajectory
                          shown={shown}
                          runLabel={runLabel}
                          bare
                          onPassing={openPassing}
                        />
                      </Fold>
                    ) : null}
                    <RawSteps steps={shown.detail?.trajectory ?? []} />
                  </div>
                ) : null}

                {work === "model" ? (
                  <Panel aria-label="What the model saw">
                    <PanelHead title="What the model saw, call by call" />
                    <div className="p-5">
                      <ModelSaw context={shown.detail?.context ?? []} />
                    </div>
                  </Panel>
                ) : null}

                {work === "decided" && shown.explain ? (
                  <HowDecided explain={shown.explain} doubts={doubtsOf(shown)} />
                ) : null}
              </>
            ) : null}

            {tryShown ? <PageFilm shown={tryShown} /> : null}
            {/* The video is in the filmstrip above. */}
            {shown.page ? <PageNotes page={shown.page} video={false} /> : null}
            {tryShown?.page ? (
              <Fold title="Each turn, side by side">
                <Turns shown={tryShown} />
              </Fold>
            ) : null}
            {test.history.length > 1 ? (
              <Panel aria-label="Earlier results">
                <div className="flex flex-wrap items-center gap-3 p-5">
                  <h2 className="m-0 text-[15px] font-semibold">Earlier results</h2>
                  <Earlier
                    history={test.history}
                    shown={run}
                    runLabel={runLabel}
                    onRunResult={onRunResult}
                  />
                  <span className="flex-1" />
                  <Button variant="ghost" onClick={() => setTab("history")}>
                    All history
                    <ArrowRight />
                  </Button>
                </div>
              </Panel>
            ) : null}
          </>
        ) : null}

        {view === "plan" ? (
          <div className="grid grid-cols-[minmax(0,1fr)_340px] items-start gap-5 max-xl:grid-cols-1">
            <Panel aria-label="What it checks">
              <PanelHead title="What it checks">
                <span className="flex-1" />
                <Button variant="secondary" onClick={() => onAsk(`Change "${test.name}": `)}>
                  <Sparkles />
                  Change with AI
                </Button>
                <Button variant="secondary" onClick={() => setEditing(true)}>
                  Edit file
                </Button>
              </PanelHead>
              <div className="px-5 pt-2 pb-3">
                {ready(plan) ? (
                  <Steps
                    steps={ready(plan)?.steps ?? []}
                    names={ready(plan)?.names ?? {}}
                    appName={appName}
                    result={false}
                    ui={uiSentences(test.page)}
                  />
                ) : plan.status === "loading" ? (
                  <p className="m-0 shimmer py-3">Reading the test…</p>
                ) : (
                  <p className="m-0 py-3 text-ink-2">Couldn't read what this test checks.</p>
                )}
              </div>
            </Panel>
            <Panel>
              <div className="flex flex-col gap-4 p-5">
                <HowItPlays scope={scope} id={id} plays={test.plays} proof={test.proof} />
                {limits.length ? (
                  <ul className="m-0 flex list-none flex-col gap-1.5 p-0 text-sm text-ink-2">
                    {limits.map((limit) => (
                      <li key={limit}>{limit}</li>
                    ))}
                  </ul>
                ) : null}
              </div>
            </Panel>
          </div>
        ) : null}

        {view === "history" ? (
          <Panel aria-label="History">
            {test.history.length ? (
              <div className="overflow-x-auto">
                <table className="w-full border-collapse">
                  <thead>
                    <tr>
                      <th className={TH}>Run</th>
                      <th className={TH}>Result</th>
                      <th className={cn(TH, "text-end")}>When</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...test.history]
                      .sort((a, b) => b.at - a.at)
                      .map((entry) => (
                        <tr
                          key={entry.run}
                          className={cn(
                            "cursor-default hover:bg-sunk",
                            entry.run === run && "bg-tint-soft",
                          )}
                        >
                          <td className={TD}>
                            <button
                              type="button"
                              className="font-medium hover:text-tint-ink focus-visible:outline-2 focus-visible:outline-tint"
                              onClick={() => {
                                setTab("result");
                                onRunResult(entry.run);
                              }}
                            >
                              {runLabel(entry.run)}
                            </button>
                          </td>
                          <td className={TD}>
                            <span className="inline-flex items-center gap-2">
                              <Dot verdict={entry.verdict} />
                              {entry.verdict ? verdictWord(entry.verdict) : "Not run"}
                            </span>
                          </td>
                          <td className={cn(TD, "text-end font-mono text-[13px] text-ink-3")}>
                            {when(entry.at)}
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="m-0 p-5 text-ink-3">Not run yet</p>
            )}
          </Panel>
        ) : null}
      </Page>
      {editing ? (
        <EditFile
          scope={scope}
          id={id}
          onClose={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            onEdited();
          }}
        />
      ) : null}
    </>
  );
}

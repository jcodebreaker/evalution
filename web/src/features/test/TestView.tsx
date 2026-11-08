import { ExternalLink, FileCode, Play } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Scope } from "../../lib/api";
import { api } from "../../lib/api";
import type { CaseFileView, Compare, Finding } from "../../lib/schema";
import type { Load } from "../../lib/use-load";
import { POLL_MS, ready, useLoad, useVisiblePoll } from "../../lib/use-load";
import { Button, buttonClass } from "../../ui/button";
import { Fold } from "../shared/group";
import { BackLink, Centered, Page, Toolbar } from "../shared/layout";
import { StaleChip } from "../shared/marks";
import { BrokenMapNote } from "../shared/next-step";
import { Earlier } from "./Earlier";
import { EditFile } from "./EditFile";
import { FindingNote } from "./FindingNote";
import type { Doubt } from "./HowDecided";
import { HowDecided } from "./HowDecided";
import { HowItPlays } from "./HowItPlays";
import { Inspector } from "./Inspector";
import { Output } from "./Output";
import { PageNotes } from "./Page";
import type { Mode } from "./Segmented";
import { Segmented } from "./Segmented";
import { Steps } from "./Steps";
import { Timeline } from "./Timeline";
import { Trajectory } from "./Trajectory";
import { TryContext } from "./TryContext";
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

/**
 * A test as a timeline: what it checks, step by step, before any run; and the latest result,
 * what the run's analysis found, the same steps with what happened under each, the trajectory
 * and the reply. Its file can be edited by hand.
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
  back,
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
  back: { label: string; onClick: () => void };
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
  const [mode, setMode] = useState<Mode | null>(null);
  const [focus, setFocus] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const key = `${id}:${run}`;
  // A try asked for in another run, shown once that run's result is.
  const jump = useRef<{ key: string; n: number; span: string | null } | null>(null);
  useEffect(() => {
    const to = jump.current?.key === key ? jump.current : null;
    jump.current = null;
    setTryN(to?.n ?? null);
    setMode(null);
    setFocus(to?.span ?? null);
  }, [key]);
  const [result, reloadResult] = useLoad(run ? `compare:${run}:${id}:${tryN ?? ""}` : null, () =>
    api.compare(run ?? "", id, tryN),
  );
  useVisiblePoll(reloadResult, POLL_MS, live || !!ready(result)?.wording);
  useVisiblePoll(reloadPlan, POLL_MS, !!ready(plan)?.wording);
  const failed = ready(result)?.verdict;
  const [finding] = useLoad(
    run && !live && (failed === "fail" || failed === "flaky") ? `finding:${run}:${id}` : null,
    () => api.finding(run ?? "", id),
  );

  if (file.status === "loading") return null;
  if (!test) return <Centered>Couldn't load this test.</Centered>;
  const shown = ready(result);
  const hasResult = !!shown && shown.tries.length > 0;
  const view: Mode = mode ?? (hasResult ? "result" : "plan");
  const steps = view === "result" && shown ? shown.steps : (ready(plan)?.steps ?? null);
  const head = (view === "result" ? shown : null) ?? ready(plan);
  const span = shown?.detail?.timeline.find((entry) => entry.id === focus) ?? null;
  // Limits in words, not where the test came from.
  const limits = (head?.limits ?? []).filter((limit) => !/^from\b/i.test(limit));

  return (
    <div className="flex min-h-0 flex-1 max-lg:flex-col">
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <Toolbar>
          <BackLink label={back.label} onClick={back.onClick} />
          <div className="flex-1" />
          <Segmented
            value={view}
            onChange={setMode}
            resultLabel={hasResult ? "Latest result" : live ? "Running…" : "Not run yet"}
            resultOff={!hasResult}
          />
          <Button variant="ghost" className="text-ink-2" onClick={() => setEditing(true)}>
            <FileCode />
            Edit file
          </Button>
          <Button
            variant="ghost"
            className="text-ink-2"
            onClick={() => onAsk(`Write variations of "${test.name}".`)}
          >
            Variations
          </Button>
          <Button disabled={going} onClick={() => onRun(test.name)}>
            <Play />
            Run
          </Button>
        </Toolbar>
        <Page>
          <div className="flex flex-col gap-2">
            <h1 className="m-0 text-[22px] leading-tight font-semibold tracking-[-0.4px]">
              {test.name}
            </h1>
            {test.about ? (
              <p className="m-0 max-w-[62ch] text-[13.5px] text-ink-2">{test.about}</p>
            ) : null}
            {limits.length || stale ? (
              <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-ink-2">
                {limits.length ? <span>{limits.join(" · ")}</span> : null}
                {stale ? <StaleChip reason={stale} /> : null}
              </div>
            ) : null}
          </div>

          {view === "plan" ? (
            <HowItPlays scope={scope} id={id} plays={test.plays} proof={test.proof} />
          ) : null}
          {view === "result" && shown ? (
            <VerdictBanner
              shown={shown}
              runLabel={runLabel}
              onTry={(n) => {
                setTryN(n);
                setFocus(null);
              }}
            />
          ) : null}
          {view === "result" && shown?.page?.broken_map ? (
            <BrokenMapNote
              target={scope.target}
              missing={[shown.page.broken_map.missing]}
              shot={shown.page.broken_map.shot_url}
              onRelearn={(text) => onAsk(text)}
            />
          ) : null}
          {view === "result" && shown?.trace_note ? (
            <p className="m-0 text-[12.5px] text-warning">{shown.trace_note}</p>
          ) : null}
          {view === "result" && ready(finding) ? (
            <FindingNote finding={ready(finding) as Finding} onSpan={setFocus} />
          ) : null}

          {steps ? (
            <Steps
              steps={steps}
              names={head?.names ?? {}}
              appName={appName}
              result={view === "result"}
              onSpan={setFocus}
              agreement={view === "result" ? shown?.agreement : undefined}
              isolation={view === "result" ? shown?.isolation : undefined}
              onLabel={
                view === "result" && shown?.try
                  ? (item, agree) => {
                      const line = Number(item.split("-")[1]);
                      void api
                        .label(shown.run, id, shown.try ?? 0, line, agree)
                        .then(reloadResult, () => null);
                    }
                  : undefined
              }
            />
          ) : plan.status === "loading" ? (
            <p className="m-0 shimmer text-[13px]">Reading the test…</p>
          ) : (
            <Centered>Couldn't read what this test checks.</Centered>
          )}
          {view === "result" && shown?.page ? <PageNotes page={shown.page} /> : null}

          {view === "result" && shown?.try ? (
            <>
              {shown.explain ? (
                <HowDecided explain={shown.explain} doubts={doubtsOf(shown)} />
              ) : null}
              {shown.trajectory ? (
                <Trajectory
                  shown={shown}
                  runLabel={runLabel}
                  onPassing={(other, n, at) => {
                    if (other === run) {
                      setTryN(n);
                      setFocus(at);
                    } else {
                      jump.current = { key: `${id}:${other}`, n, span: at };
                      onRunResult(other);
                    }
                  }}
                />
              ) : null}
              {shown.output &&
              (shown.output.reply || shown.output.reference || shown.output.must.length) ? (
                <Output shown={shown} />
              ) : null}
              <div className="flex flex-wrap items-center gap-2">
                {shown.outcome === "failed" ? (
                  <Button
                    variant="secondary"
                    aria-label="Why did it fail?"
                    onClick={() =>
                      onAsk(
                        `Why did "${test.name}" fail on try ${shown.try} of ${runLabel(shown.run)}?`,
                        shown.run,
                      )
                    }
                  >
                    Why?
                  </Button>
                ) : null}
                {shown.trace_url ? (
                  <a
                    href={shown.trace_url}
                    target="_blank"
                    rel="noreferrer"
                    className={buttonClass("secondary")}
                  >
                    <ExternalLink />
                    Open the trace
                  </a>
                ) : null}
              </div>
              {shown.detail?.timeline.length || shown.detail?.context.length ? (
                <Fold title="Show steps">
                  <div className="flex flex-col gap-3">
                    <TryContext
                      context={shown.detail.context}
                      trajectory={shown.detail.trajectory}
                    />
                    {shown.detail.timeline.length ? (
                      <Timeline
                        spans={shown.detail.timeline}
                        selected={focus}
                        onSelect={setFocus}
                      />
                    ) : null}
                  </div>
                </Fold>
              ) : null}
              {test.history.length > 1 ? (
                <Earlier
                  history={test.history}
                  shown={run}
                  runLabel={runLabel}
                  onRunResult={onRunResult}
                />
              ) : null}
            </>
          ) : null}
        </Page>
      </div>
      {span && span.kind !== "missing" ? (
        <Inspector span={span} onClose={() => setFocus(null)} />
      ) : null}
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
    </div>
  );
}

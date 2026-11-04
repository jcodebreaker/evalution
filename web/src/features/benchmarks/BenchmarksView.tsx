import { ExternalLink } from "lucide-react";
import { useState } from "react";
import { api, errorText } from "../../lib/api";
import { cn } from "../../lib/cn";
import { seconds, tokens, usd, when } from "../../lib/format";
import type { Benchmarks } from "../../lib/schema";
import { POLL_MS, useLoad, useVisiblePoll } from "../../lib/use-load";
import { Button } from "../../ui/button";
import { Skeleton } from "../../ui/field";
import { Fold, Group, GroupHeading, TextLink } from "../shared/group";
import { Centered, Page, Toolbar, ToolbarTitle } from "../shared/layout";
import type { Run } from "./Glance";
import { Glance, played, StatStrip, share, Trend } from "./Glance";

type Question = Run["questions"][number];
type Filter = "all" | "wrong" | "right" | "other" | "waiting";

/** A benchmark's test set stays apart from the app's own tests. */
export function isBenchmark(set: string): boolean {
  return set.startsWith("longmemeval-");
}

const KINDS: Record<string, string> = {
  "single-session-user": "Single-session facts",
  "single-session-preference": "Preferences",
  "multi-session": "Across conversations",
  "temporal-reasoning": "Dates and order",
  "knowledge-update": "Updated facts",
};
const SIZES: Record<string, string> = {
  quick: "Quick",
  oracle: "Full",
  s: "Full, S",
  m: "Full, M",
};
const FILTERS: [Filter, string][] = [
  ["all", "All"],
  ["wrong", "Wrong"],
  ["right", "Right"],
  ["other", "Not judged"],
  ["waiting", "Waiting"],
];

/** How a run plays, in a few words: `One conversation · Full, S`. */
function playedAs(set: string, boundary: string | undefined): string {
  const size = set.replace("longmemeval-", "").replace("-same", "");
  return `${boundary === "same" ? "One conversation · " : ""}${SIZES[size] ?? size}`;
}

function didntFinish(run: Run): boolean {
  return run.status === "stopped" || run.status === "failed";
}

function outcome(question: Question): { word: string; tone: string } {
  if (question.verdict === "pass") {
    return { word: question.unanswerable ? "Said it didn't know" : "Right", tone: "text-success" };
  }
  if (question.verdict === "fail" || question.verdict === "flaky") {
    if (question.unanswerable) return { word: "Made it up", tone: "text-destructive" };
    if (question.memory?.reached) return { word: "Had it, answered wrong", tone: "text-warning" };
    if (question.memory?.written === false) {
      return { word: "Never saved", tone: "text-destructive" };
    }
    if (question.memory?.committed === false) {
      return { word: "Not committed", tone: "text-warning" };
    }
    if (question.memory?.committed) return { word: "Saved, not used", tone: "text-warning" };
    return { word: "Wrong", tone: "text-destructive" };
  }
  if (question.verdict === "unjudged") return { word: "Not judged", tone: "text-warning" };
  return { word: "Waiting", tone: "text-ink-3" };
}

function shown(question: Question, filter: Filter): boolean {
  const { verdict } = question;
  const wrong = verdict === "fail" || verdict === "flaky";
  if (filter === "right") return verdict === "pass";
  if (filter === "wrong") return wrong;
  if (filter === "other") return verdict === "unjudged";
  const answered = verdict === "pass" || wrong || verdict === "unjudged";
  return filter === "all" ? answered : !answered;
}

/** LongMemEval on this app: what its runs came to, how each number adds up, and each question. */
export function BenchmarksView({
  target,
  selected,
  onPick,
  onRun,
  onTry,
}: {
  target: string;
  /** The run to show; left out, the newest. */
  selected: string | null;
  onPick: (run: string) => void;
  onRun: (set: string) => void;
  onTry: (set: string, run: string, id: string) => void;
}) {
  const [state, reload] = useLoad(target, () => api.benchmarks(target));
  const data = state.status === "ready" ? state.data : null;
  const going = data?.runs.some((run) => run.status === "running") ?? false;

  useVisiblePoll(reload, POLL_MS, going);

  if (state.status === "error") return <Centered>Couldn't read the benchmarks.</Centered>;
  if (!data) {
    return (
      <div className="mx-auto flex w-full max-w-[860px] flex-col gap-6 px-6 pt-16">
        <Skeleton className="h-64 rounded-xl" />
      </div>
    );
  }
  // Left to choose, a run that got somewhere: not one stopped before anything was judged.
  const run =
    data.runs.find((entry) => entry.run === selected) ??
    data.runs.find((entry) => entry.status === "running" || played(entry) > 0) ??
    data.runs[0] ??
    null;
  const plan = (
    <PlanCard
      target={target}
      plan={data.plan}
      ran={data.runs.length > 0}
      going={going}
      onRun={onRun}
      onChanged={reload}
    />
  );
  // A plan with no run yet, or one waiting for approval, is what there is to do: it goes first.
  const awaiting = !run || (data.plan !== null && !data.plan.approved);
  return (
    <>
      <Toolbar>
        <ToolbarTitle title="Benchmarks" sub="LongMemEval" />
      </Toolbar>
      <Page>
        {awaiting ? plan : null}
        {run ? <RunResult key={run.run} run={run} onTry={onTry} onStopped={reload} /> : null}
        {run ? <RunList runs={data.runs} selected={run} onPick={onPick} /> : null}
        {awaiting ? null : plan}
        <Fold title="Other sizes">
          <Sizes target={target} sizes={data.sizes} onRun={onRun} onInstalled={reload} />
        </Fold>
      </Page>
    </>
  );
}

function hours(value: number): string {
  return value < 1 ? "under an hour" : `about ${Math.round(value)} hours`;
}

/** How the benchmark will play on this app, why, and the way to start it. Once approved and run, one line. */
function PlanCard({
  target,
  plan,
  ran,
  going,
  onRun,
  onChanged,
}: {
  target: string;
  plan: Benchmarks["plan"];
  ran: boolean;
  going: boolean;
  onRun: (set: string) => void;
  onChanged: () => Promise<void> | void;
}) {
  const [working, setWorking] = useState<"planning" | "approving" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const act = async (what: "planning" | "approving", work: () => Promise<void>) => {
    setWorking(what);
    setError(null);
    try {
      await work();
      await onChanged();
    } catch (failed) {
      setError(errorText(failed, "That didn't work. Try again."));
    } finally {
      setWorking(null);
    }
  };
  const makePlan = () =>
    act("planning", async () => {
      await api.planBenchmark(target);
    });
  const approve = () =>
    act("approving", async () => {
      const made = await api.approveBenchmarkPlan(target);
      onRun(made.set);
    });
  const planning = working === "planning";
  const again = (
    <Button
      variant={plan || ran ? "ghost" : "default"}
      size="sm"
      disabled={working !== null || going}
      onClick={() => void makePlan()}
    >
      {planning ? "Planning and trying it" : plan ? "Plan again" : "Plan how to run this"}
    </Button>
  );
  const failure = error ? <p className="m-0 text-[13px] text-destructive">{error}</p> : null;
  if (!plan) {
    if (ran) {
      return (
        <div className="flex flex-col items-start gap-2">
          {again}
          {failure}
        </div>
      );
    }
    return (
      <section>
        <GroupHeading title="How this will run on your app" />
        <Group className="flex flex-col items-start gap-3 px-4 py-3.5">
          <p className="m-0 text-[13.5px] text-ink-2">
            Picks how to play it on this app and tries a few real messages. You approve before
            anything long runs.
          </p>
          {again}
          {failure}
        </Group>
      </section>
    );
  }
  const works = plan.verdict === "run" && plan.probe?.ok === true;
  const warnings = (
    <>
      {plan.stale ? (
        <p className="m-0 font-semibold text-warning">
          What evalution knows of this app has changed since this plan. Plan again.
        </p>
      ) : null}
      {plan.verdict === "cannot" ? (
        <p className="m-0 font-semibold text-warning">This can't be measured on this app.</p>
      ) : null}
    </>
  );
  const details = (
    <dl className="m-0 flex flex-col gap-3">
      <Said label="Why" text={plan.why} />
      <Said label="What the score will mean" text={plan.measures} />
      {plan.probe ? (
        <Said
          label="Tried"
          text={
            plan.probe.ok
              ? `It played through${plan.probe.seconds_per_message ? `, ${plan.probe.seconds_per_message} s a message` : ""}. It ${plan.probe.answered ? "answered" : "didn't answer"} the test question. Memory ${plan.probe.memory_readable ? "can" : "can't"} be read.`
              : `It didn't play through: ${plan.probe.error ?? "no reason given"}`
          }
        />
      ) : null}
      {plan.questions && plan.messages ? (
        <Said
          label="Size"
          text={`${plan.questions} questions, ${plan.messages.toLocaleString()} messages${plan.hours ? `, ${hours(plan.hours)} at least. Keep this computer awake.` : "."}`}
        />
      ) : null}
    </dl>
  );
  const sources = (
    <Fold title="What it read, and what to watch for">
      <div className="flex flex-col gap-2.5 ps-5">
        {plan.evidence.map((quote) => (
          <blockquote key={quote} className="m-0 border-s-2 border-separator ps-3 text-ink-2">
            {quote}
          </blockquote>
        ))}
        {plan.warnings.length ? (
          <ul className="m-0 flex flex-col gap-1 ps-4">
            {plan.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        ) : null}
      </div>
    </Fold>
  );
  const actions = (
    <div className="flex flex-wrap items-center gap-2">
      {works ? (
        <Button
          size="sm"
          disabled={working !== null || going || plan.stale}
          onClick={() => void approve()}
        >
          {working === "approving" ? "Getting it ready" : "Approve and run"}
        </Button>
      ) : null}
      {again}
    </div>
  );
  // A plan still waiting for approval stays open, whatever has run before it: it has to be read.
  if (ran && plan.approved) {
    return (
      <Group className="flex flex-col gap-2 px-3 py-2.5 text-[13.5px]">
        {warnings}
        <Fold title={<span className="font-semibold text-foreground">{planLine(plan)}</span>}>
          <div className="flex flex-col gap-3 ps-5">
            <p className="m-0 font-semibold">{plan.how}</p>
            {details}
            {sources}
            {actions}
          </div>
        </Fold>
        {failure}
      </Group>
    );
  }
  return (
    <section>
      <GroupHeading title="How this will run on your app" />
      <Group className="flex flex-col gap-3 px-4 py-3.5 text-[13.5px]">
        {warnings}
        <p className="m-0 font-semibold">{plan.how}</p>
        {details}
        {sources}
        {actions}
        {failure}
      </Group>
    </section>
  );
}

function planLine(plan: NonNullable<Benchmarks["plan"]>): string {
  if (plan.verdict === "cannot") return "Can't be measured on this app";
  const way = plan.boundary === "same" ? "One conversation" : "New conversations";
  const size = SIZES[plan.size ?? ""] ?? plan.size;
  const length = plan.hours
    ? plan.hours < 1
      ? "under 1 h"
      : `≈${Math.round(plan.hours)} h`
    : null;
  return [way, size, plan.questions ? `${plan.questions} questions` : null, length]
    .filter(Boolean)
    .join(" · ");
}

function RunResult({
  run,
  onTry,
  onStopped,
}: {
  run: Run;
  onStopped: () => Promise<void> | void;
  onTry: (set: string, run: string, id: string) => void;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const { report } = run;
  const rows = run.questions.filter((question) => shown(question, filter));
  const kinds = Object.entries(report.kinds).filter(([, tally]) => tally.judged > 0);
  const anyPlayed = played(run) > 0;
  return (
    <>
      <section className="flex flex-col gap-2">
        <GroupHeading
          title={[
            run.started_at ? when(run.started_at) : null,
            playedAs(run.set, run.plan?.boundary),
          ]
            .filter(Boolean)
            .join(" · ")}
          aside={
            didntFinish(run) ? (
              <span className="font-semibold text-warning">Didn't finish</span>
            ) : null
          }
        />
        <Glance run={run} onStopped={onStopped} />
        <StatStrip run={run} />
      </section>
      {report.memory.measured ? <Lost memory={report.memory} /> : null}
      {kinds.length ? (
        <section>
          <GroupHeading title="By kind of question" />
          <Bars
            rows={kinds.map(([kind, tally]) => ({
              name: KINDS[kind] ?? kind,
              value: tally.right,
              of: tally.judged,
            }))}
          />
        </section>
      ) : null}
      {anyPlayed ? (
        <section>
          <div className="flex flex-wrap items-center gap-2 px-1 pb-1.5">
            <h3 className="m-0 text-[13px] font-semibold">Questions</h3>
            <div className="ms-auto flex flex-wrap gap-1">
              {FILTERS.map(([key, label]) => {
                const many = run.questions.filter((question) => shown(question, key)).length;
                return key === "all" || many ? (
                  <button
                    key={key}
                    type="button"
                    aria-pressed={filter === key}
                    onClick={() => setFilter(key)}
                    className={cn(
                      "rounded-full px-3 py-1 text-[12.5px] focus-visible:outline-2 focus-visible:outline-ring",
                      filter === key
                        ? "bg-selection-strong font-semibold text-tint"
                        : "text-ink-2 hover:bg-selection",
                    )}
                  >
                    {label} <span className="tabular-nums opacity-70">{many}</span>
                  </button>
                ) : null;
              })}
            </div>
          </div>
          <Group>
            {rows.map((question) => (
              <QuestionRow
                key={question.case}
                question={question}
                onOpen={() => onTry(run.set, run.run, question.case)}
              />
            ))}
          </Group>
        </section>
      ) : null}
    </>
  );
}

function Bars({ rows }: { rows: { name: string; value: number; of: number }[] }) {
  return (
    <Group className="flex flex-col gap-3 px-4 py-3.5">
      {rows.map(({ name, value, of }) => (
        <div
          key={name}
          className="grid grid-cols-[minmax(0,11em)_minmax(0,1fr)_4.5em] items-center gap-3 text-[13.5px]"
        >
          <span className="truncate">{name}</span>
          <div className="h-2 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-tint"
              style={{ width: `${(value / of) * 100}%` }}
            />
          </div>
          <span className="text-end tabular-nums text-ink-2">
            {value} of {of}
          </span>
        </div>
      ))}
    </Group>
  );
}

/** How far the facts got: each step counted over the same questions. */
function Lost({ memory }: { memory: Run["report"]["memory"] }) {
  const of = memory.measured;
  return (
    <section>
      <GroupHeading title="How far facts got" />
      <Bars
        rows={[
          { name: "Told the agent", value: of, of },
          { name: "Written to memory", value: memory.written, of },
          { name: "Committed", value: memory.committed, of },
          ...(memory.reach_measured
            ? [{ name: "Reached the model", value: memory.reached, of: memory.reach_measured }]
            : []),
          { name: "Answered right", value: memory.used, of },
        ]}
      />
    </section>
  );
}

/** Only a web address goes in a link; a stored run is data. */
function traceLink(url: string | null): string | null {
  return url && /^https?:\/\//i.test(url) ? url : null;
}

function QuestionRow({ question, onOpen }: { question: Question; onOpen: () => void }) {
  const { word, tone } = outcome(question);
  const trace = traceLink(question.trace_url);
  const figures = [
    question.tokens ? `${tokens(question.tokens)} tokens` : null,
    question.seconds ? seconds(question.seconds) : null,
  ].filter(Boolean);
  const more = [
    question.messages ? `${question.messages} messages` : null,
    question.usd ? usd(question.usd) : null,
    ...question.models,
  ].filter(Boolean);
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-t border-separator px-4 py-2.5 first:border-t-0">
      <Fold
        className="min-w-0 flex-1 basis-72"
        title={
          <span className="flex min-w-0 flex-wrap items-baseline gap-x-2 text-[13.5px]">
            <span className="min-w-0 text-foreground">{question.question}</span>
            <span className={cn("font-semibold", tone)}>{word}</span>
            <span className="text-ink-3">
              {question.unanswerable ? "Unanswerable" : (KINDS[question.kind] ?? question.kind)}
            </span>
          </span>
        }
      >
        <dl className="m-0 flex flex-col gap-2.5 ps-5 text-[13.5px]">
          <Said label="What counts as right" text={question.right_is} />
          <Said label="The reply" text={question.reply || "–"} />
          {question.reason ? <Said label="Why" text={question.reason} /> : null}
          {question.error ? <Said label="What went wrong" text={question.error} /> : null}
          {more.length ? <dd className="m-0 text-[12px] text-ink-2">{more.join(" · ")}</dd> : null}
          <dd className="m-0">
            <TextLink onClick={onOpen}>Open the whole try</TextLink>
          </dd>
        </dl>
      </Fold>
      {figures.length || trace ? (
        <div className="ms-auto flex shrink-0 items-baseline gap-3 ps-5 text-[12px] text-ink-3 tabular-nums">
          {figures.join(" · ")}
          {trace ? (
            <a
              href={trace}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 font-semibold text-tint hover:underline focus-visible:outline-2 focus-visible:outline-ring"
            >
              Trace
              <ExternalLink className="size-3" aria-hidden />
            </a>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function Said({ label, text }: { label: string; text: string }) {
  return (
    <div>
      <dt className="text-[12px] font-semibold text-ink-2">{label}</dt>
      <dd className="m-0 whitespace-pre-wrap [overflow-wrap:anywhere]">{text}</dd>
    </div>
  );
}

function Sizes({
  target,
  sizes,
  onRun,
  onInstalled,
}: {
  target: string;
  sizes: Benchmarks["sizes"];
  onRun: (set: string) => void;
  onInstalled: () => Promise<void> | void;
}) {
  const [working, setWorking] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const start = async (size: string, set: string, ready: boolean) => {
    setError(null);
    if (!ready) {
      setWorking(size);
      try {
        await api.installBenchmark(target, size);
        await onInstalled();
      } catch (failed) {
        setError(errorText(failed, "Couldn't get the benchmark ready."));
        return;
      } finally {
        setWorking(null);
      }
    }
    onRun(set);
  };
  return (
    <section className="ps-5">
      <Group>
        {sizes.map((entry) => (
          <div
            key={entry.size}
            className="flex flex-wrap items-center gap-3 border-t border-separator px-4 py-2.5 first:border-t-0"
          >
            <div className="min-w-0 flex-1 basis-60">
              <div className="text-[13.5px] font-semibold">
                {SIZES[entry.size] ?? entry.size}
                {entry.questions ? (
                  <span className="font-normal text-ink-2"> · {entry.questions} questions</span>
                ) : null}
              </div>
              <div className="text-[12.5px] text-ink-2">{entry.about}</div>
            </div>
            <Button
              variant="secondary"
              size="sm"
              disabled={working !== null}
              onClick={() => void start(entry.size, entry.set, entry.questions !== null)}
            >
              {working === entry.size ? "Getting it ready" : "Run"}
            </Button>
          </div>
        ))}
      </Group>
      {error ? <p className="m-0 px-1 pt-2 text-[13px] text-destructive">{error}</p> : null}
    </section>
  );
}

function RunList({
  runs,
  selected,
  onPick,
}: {
  runs: Run[];
  selected: Run;
  onPick: (run: string) => void;
}) {
  const listed = runs.filter((run) => run.status === "running" || played(run) > 0);
  if (!listed.some((run) => run.run !== selected.run)) return null;
  // Newest first on the screen, oldest first on the line.
  const points = listed
    .filter((run) => run.set === selected.set && run.report.accuracy !== null)
    .map((run) => ({
      at: run.started_at ? when(run.started_at) : run.run,
      value: run.report.accuracy ?? 0,
    }))
    .reverse();
  return (
    <section>
      <GroupHeading
        title="Earlier runs"
        aside={points.length > 1 ? <Trend points={points} /> : null}
      />
      <Group>
        {listed.map((run) => (
          <button
            key={run.run}
            type="button"
            aria-current={run.run === selected.run ? "true" : undefined}
            onClick={() => onPick(run.run)}
            className={cn(
              "flex w-full flex-wrap items-baseline gap-x-3 border-t border-separator px-4 py-2.5 text-start text-[13.5px] first:border-t-0 hover:bg-selection focus-visible:outline-2 focus-visible:outline-ring",
              run.run === selected.run && "bg-selection-strong",
            )}
          >
            <span className="font-semibold">{run.started_at ? when(run.started_at) : "–"}</span>
            <span className="text-ink-2">{playedAs(run.set, run.plan?.boundary)}</span>
            {run.status === "running" ? <span className="text-ink-2">Running</span> : null}
            {didntFinish(run) ? <span className="text-ink-3">Didn't finish</span> : null}
            <span className="ms-auto tabular-nums">
              <span className="font-display font-bold">{share(run.report.accuracy)}</span>
              {run.report.judged ? (
                <span className="text-ink-2">
                  {" "}
                  · {run.report.right} of {run.report.judged}
                </span>
              ) : null}
            </span>
          </button>
        ))}
      </Group>
    </section>
  );
}

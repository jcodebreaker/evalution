import {
  ArrowDown,
  ArrowUp,
  ChartPie,
  ChevronRight,
  Eye,
  Layers,
  ListFilter,
  Sparkles,
  Timer,
  TrendingUp,
} from "lucide-react";
import { useState } from "react";
import { api, errorText } from "../../lib/api";
import { cn } from "../../lib/cn";
import { seconds, tokens, usd, when } from "../../lib/format";
import type { Benchmarks, Verdict } from "../../lib/schema";
import { POLL_MS, useLoad, useVisiblePoll } from "../../lib/use-load";
import { Button } from "../../ui/button";
import { Skeleton } from "../../ui/field";
import { Fold, Group, GroupHeading, TextLink } from "../shared/group";
import { Centered, Page } from "../shared/layout";
import { VerdictGlyph } from "../shared/marks";
import {
  BarChart,
  ChartPanel,
  ColumnChart,
  FlowChart,
  LineChart,
  Outcomes,
  PromptChart,
} from "./Charts";
import type { Run } from "./Glance";
import { benchmarkName, Glance, played, share } from "./Glance";

type Question = Run["questions"][number];
type Filter = "all" | "wrong" | "right" | "other" | "waiting";

/** A benchmark's test set stays apart from the app's own tests. */
export function isBenchmark(set: string): boolean {
  return set.startsWith("longmemeval-");
}

/** The three datasets a person chooses between, each with its own runs. */
const DATASETS = [
  {
    key: "short",
    label: "Short histories",
    about:
      "Does it save a fact and recall it later? About 11 messages a question, holding only what it needs to know.",
  },
  {
    key: "middle",
    label: "Middle histories",
    about:
      "Can it still answer once older chat is folded away? About 50 messages a question, in one conversation.",
  },
  {
    key: "long",
    label: "Long histories",
    about:
      "Can it still find a fact buried in a long chat? About 270 messages a question, mostly unrelated.",
  },
] as const;

type Dataset = (typeof DATASETS)[number]["key"];

function datasetOf(set: string): Dataset {
  if (set.startsWith("longmemeval-middle")) return "middle";
  return /^longmemeval-(s|m)(-|$)/.test(set) ? "long" : "short";
}

const SHORT_CHARS = 110;
const REPLY_CHARS = 220;

type Tab = "overview" | "questions" | "runs" | "setup";
const TABS: [Tab, string][] = [
  ["overview", "Overview"],
  ["questions", "Questions"],
  ["runs", "Runs"],
  ["setup", "Setup"],
];

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
  ["other", "Judge unsure"],
  ["waiting", "Waiting"],
];

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
  if (question.verdict === "unjudged")
    return {
      word: question.leaning ? `Unsure, leaned ${question.leaning}` : "Not judged",
      tone: "text-warning",
    };
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
  onAsk,
  onRun,
  onTry,
}: {
  target: string;
  /** The run to show; left out, the newest. */
  selected: string | null;
  onPick: (run: string) => void;
  onAsk: (text: string, run: string) => void;
  onRun: (set: string) => void;
  onTry: (set: string, run: string, id: string) => void;
}) {
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
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
  const goingIn = data.runs.find((entry) => entry.status === "running");
  const shownDataset =
    dataset ?? (run ? datasetOf(run.set) : goingIn ? datasetOf(goingIn.set) : DATASETS[0].key);
  const inDataset = data.runs.filter((entry) => datasetOf(entry.set) === shownDataset);
  const active =
    inDataset.find((entry) => entry.run === selected) ??
    inDataset.find((entry) => entry.status === "running" || played(entry) > 0) ??
    null;
  const runDataset = async (key: Dataset) => {
    setDataset(key);
    if (key === "long") {
      if (data.plan?.approved && data.plan.set) onRun(data.plan.set);
      else setTab("setup");
      return;
    }
    const size = key === "middle" ? "middle" : "quick";
    const entry = data.sizes.find((listed) => listed.size === size);
    if (!entry) return;
    if (entry.questions === null) await api.installBenchmark(target, size);
    onRun(entry.set);
  };
  const listed = inDataset.filter((entry) => entry.status === "running" || played(entry) > 0);
  // With no run yet there's nothing to tab between: only the way to run it.
  const shownTab: Tab = active ? tab : "setup";
  return (
    <Page wide>
      <h1 className="m-0 -mb-3 px-1 text-[22px] font-semibold tracking-[-0.02em]">Benchmarks</h1>
      <div className="grid grid-cols-3 gap-3 max-lg:grid-cols-1">
        {DATASETS.map((entry) => {
          const own = data.runs.filter((other) => datasetOf(other.set) === entry.key);
          const latest = own.find((other) => other.report.judged > 0);
          const mine = own.find((other) => other.status === "running");
          return (
            <div
              key={entry.key}
              className={cn(
                "group-card flex min-w-0 flex-col gap-2 p-3.5",
                shownDataset === entry.key && "border-chart! ring-1 ring-chart",
              )}
            >
              <button
                type="button"
                aria-pressed={shownDataset === entry.key}
                onClick={() => {
                  setDataset(entry.key);
                  onPick("");
                }}
                className="flex min-w-0 flex-col gap-1 text-start focus-visible:outline-2 focus-visible:outline-ring"
              >
                <span className="flex items-baseline gap-2">
                  <span className="text-[14px] font-semibold">{entry.label}</span>
                  {latest?.report.accuracy != null ? (
                    <span className="ms-auto text-[20px] leading-none font-semibold tracking-[-0.02em] tabular-nums">
                      {share(latest.report.accuracy)}
                    </span>
                  ) : null}
                </span>
                <span className="text-[12.5px] text-ink-2">{entry.about}</span>
              </button>
              <div className="flex min-h-7 items-center gap-2 text-[12px] text-ink-2 tabular-nums">
                {mine ? (
                  <span className="font-medium text-tint">
                    Running, {played(mine)} of {mine.questions.length} questions done
                  </span>
                ) : going ? (
                  <span>Can run when the current one ends</span>
                ) : (
                  <Button size="sm" variant="secondary" onClick={() => void runDataset(entry.key)}>
                    Run
                  </Button>
                )}
                {latest?.report.judged ? (
                  <span className="ms-auto">
                    {latest.report.right} of {latest.report.judged} right
                  </span>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
      {active ? (
        <div
          role="tablist"
          aria-label="This dataset"
          className="flex flex-wrap gap-1 border-b border-separator"
        >
          {TABS.map(([key, label]) => {
            const many =
              key === "questions"
                ? active
                  ? played(active)
                  : 0
                : key === "runs"
                  ? listed.length
                  : 0;
            return (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={shownTab === key}
                onClick={() => setTab(key)}
                className={cn(
                  "-mb-px border-b-2 px-3 py-2 text-[13.5px] font-medium focus-visible:outline-2 focus-visible:outline-ring",
                  shownTab === key
                    ? "border-tint text-foreground"
                    : "border-transparent text-ink-2 hover:text-foreground",
                )}
              >
                {label}
                {many ? <span className="ms-1.5 text-ink-3 tabular-nums">{many}</span> : null}
              </button>
            );
          })}
        </div>
      ) : null}
      {active && played(active) > 0 && shownTab !== "setup" ? (
        <AskTraces run={active} onAsk={onAsk} />
      ) : null}
      {shownTab === "setup" ? <HowItRuns dataset={shownDataset} /> : null}
      {shownTab === "setup" && shownDataset === "long" ? (
        <PlanCard
          target={target}
          plan={data.plan}
          ran={inDataset.length > 0}
          going={going}
          onRun={onRun}
          onChanged={reload}
        />
      ) : null}
      {shownTab === "runs" && active ? (
        <RunList
          runs={inDataset}
          selected={active}
          onPick={(id) => {
            onPick(id);
            setTab("overview");
          }}
        />
      ) : null}
      {active && (shownTab === "overview" || shownTab === "questions") ? (
        <RunResult
          key={active.run}
          run={active}
          runs={inDataset}
          tab={shownTab}
          onPick={onPick}
          onTry={onTry}
          onStopped={reload}
        />
      ) : null}
    </Page>
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
        <Group className="flex flex-col items-start gap-3 p-6 max-md:p-5">
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
      {works && !going ? (
        <Button size="sm" disabled={working !== null || plan.stale} onClick={() => void approve()}>
          {working === "approving"
            ? "Getting it ready"
            : plan.approved
              ? "Run again"
              : "Approve and run"}
        </Button>
      ) : null}
      {again}
    </div>
  );
  // A plan still waiting for approval stays open, whatever has run before it: it has to be read.
  if (ran && plan.approved) {
    return (
      <div className="flex flex-col gap-2 text-[13.5px]">
        {warnings}
        <Fold title={planLine(plan)}>
          <div className="flex flex-col gap-3 ps-5">
            <p className="m-0 font-semibold">{plan.how}</p>
            {details}
            {sources}
            {actions}
          </div>
        </Fold>
        {failure}
      </div>
    );
  }
  return (
    <section>
      <GroupHeading title="How this will run on your app" />
      <Group className="flex flex-col gap-3 p-6 text-[13.5px] max-md:p-5">
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

const ASK_STARTERS = [
  "Why did it get these wrong?",
  "Where did the tokens go?",
  "Which models did it use?",
];

/** Ask the agent about this run: it reads the run and its traces. */
const SETUPS: Record<
  Dataset,
  { questions: string; messages: string; history: string; memory: string }
> = {
  short: {
    questions: "5",
    messages: "About 11",
    history: "Only the chats holding the answer. The conversation is cleared between chats.",
    memory: "Only what it saved can answer.",
  },
  middle: {
    questions: "10",
    messages: "About 50",
    history: "The chats holding the answer, then about 40 unrelated messages, in one conversation.",
    memory: "Your app folds older chat, so the raw messages are gone.",
  },
  long: {
    questions: "Set by the plan",
    messages: "About 270",
    history: "The chats holding the answer among many unrelated ones, in one conversation.",
    memory: "It folds on its own, as in real use.",
  },
};

/** How a dataset plays, for someone who hasn't run it yet. */
function HowItRuns({ dataset }: { dataset: Dataset }) {
  const setup = SETUPS[dataset];
  const steps = [
    ["Send the history", setup.history],
    ["Check memory", "Is the fact saved? Measured, and never decides the score."],
    ["Ask the question", setup.memory],
    ["Judge the reply", "The grader compares it with the known answer: right or wrong."],
  ];
  return (
    <section className="group-card flex flex-col gap-4 p-4">
      <dl className="m-0 flex flex-wrap gap-x-8 gap-y-2">
        <div>
          <dt className="text-[12px] text-ink-2">Questions</dt>
          <dd className="m-0 text-[20px] font-semibold tabular-nums">{setup.questions}</dd>
        </div>
        <div>
          <dt className="text-[12px] text-ink-2">Messages each</dt>
          <dd className="m-0 text-[20px] font-semibold tabular-nums">{setup.messages}</dd>
        </div>
      </dl>
      <ol className="m-0 grid list-none grid-cols-4 gap-3 p-0 max-lg:grid-cols-2 max-sm:grid-cols-1">
        {steps.map(([title, detail], index) => (
          <li key={title} className="flex min-w-0 flex-col gap-1 border-t border-separator pt-2.5">
            <span className="text-[13px] font-medium">
              {index + 1}. {title}
            </span>
            <span className="text-[12.5px] text-ink-2">{detail}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function AskTraces({ run, onAsk }: { run: Run; onAsk: (text: string, run: string) => void }) {
  const [text, setText] = useState("");
  const send = (asked: string) => {
    const clean = asked.trim();
    if (!clean) return;
    onAsk(`About benchmark run ${run.run}, reading its traces: ${clean}`, run.run);
    setText("");
  };
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        send(text);
      }}
      className="group-card flex flex-col gap-2.5 p-3"
    >
      <div className="flex items-center gap-2 rounded-lg border border-input bg-background px-2.5 py-1.5 focus-within:border-chart focus-within:ring-1 focus-within:ring-chart">
        <Sparkles className="size-4 shrink-0 text-tint" aria-hidden />
        <input
          id="ask-traces"
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="Ask anything about this run's traces"
          aria-label="Ask anything about this run's traces"
          className="min-w-0 flex-1 bg-transparent text-[13.5px] outline-none placeholder:text-ink-3"
        />
        <Button type="submit" size="sm" disabled={!text.trim()}>
          Ask
        </Button>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {ASK_STARTERS.map((starter) => (
          <button
            key={starter}
            type="button"
            onClick={() => send(starter)}
            className="rounded-full px-2.5 py-1 text-[12px] text-ink-2 ring-1 ring-separator hover:bg-selection focus-visible:outline-2 focus-visible:outline-ring"
          >
            {starter}
          </button>
        ))}
      </div>
    </form>
  );
}

function RunResult({
  run,
  runs,
  tab,
  onPick,
  onTry,
  onStopped,
}: {
  run: Run;
  /** Every run, newest first. */
  runs: Run[];
  tab: "overview" | "questions";
  onPick: (run: string) => void;
  onStopped: () => Promise<void> | void;
  onTry: (set: string, run: string, id: string) => void;
}) {
  const [filter, setFilter] = useState<Filter>("all");
  const { report } = run;
  const rows = run.questions.filter((question) => shown(question, filter));
  const kinds = Object.entries(report.kinds).filter(([, tally]) => tally.judged > 0);
  const anyPlayed = played(run) > 0;
  const sameSet = runs.filter((entry) => entry.set === run.set);
  const previous =
    sameSet.slice(sameSet.indexOf(run) + 1).find((entry) => played(entry) > 0) ?? null;
  const trend = sameSet.filter((entry) => entry.report.accuracy !== null).reverse();
  const timed = run.questions.filter((question) => question.seconds);
  const { memory } = report;
  const prompted = run.questions.filter((question) => question.prompts.length > 1);
  return (
    <>
      {tab === "overview" ? <Glance run={run} previous={previous} onStopped={onStopped} /> : null}
      {tab === "questions" && anyPlayed ? (
        <section>
          <div className="flex flex-wrap items-center gap-2 px-1 pb-2.5">
            <h3 className="m-0 text-[15px] font-semibold">Questions</h3>
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
          <QuestionTable rows={rows} onOpen={(id) => onTry(run.set, run.run, id)} />
        </section>
      ) : null}
      {tab === "overview" && anyPlayed ? (
        <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,400px),1fr))] gap-3">
          {trend.length > 1 ? (
            <ChartPanel icon={TrendingUp} title="Answer accuracy by run" unit="% right">
              <LineChart
                name="Answer accuracy by run"
                onPick={onPick}
                points={trend.map((entry) => ({
                  id: entry.run,
                  at: entry.started_at
                    ? new Date(entry.started_at * 1000).toLocaleDateString(undefined, {
                        month: "short",
                        day: "numeric",
                      })
                    : "",
                  value: entry.report.accuracy ?? 0,
                  label: share(entry.report.accuracy),
                  detail: `${entry.report.right} of ${entry.report.judged}${entry.started_at ? `, ${when(entry.started_at)}` : ""}`,
                }))}
              />
            </ChartPanel>
          ) : null}
          {memory.measured ? (
            <ChartPanel icon={ListFilter} title="How far facts got" unit="% of questions">
              <FlowChart
                steps={[
                  { name: "Told", value: memory.measured, of: memory.measured },
                  { name: "Written", value: memory.written, of: memory.measured },
                  { name: "Committed", value: memory.committed, of: memory.measured },
                  ...(memory.reach_measured
                    ? [{ name: "Reached", value: memory.reached, of: memory.reach_measured }]
                    : []),
                  { name: "Right", value: memory.used, of: memory.measured },
                ]}
              />
            </ChartPanel>
          ) : null}
          {kinds.length ? (
            <ChartPanel icon={Layers} title="By kind of question" unit="% right">
              <BarChart
                head="Kind"
                rows={kinds
                  .map(([kind, tally]) => ({
                    name: KINDS[kind] ?? kind,
                    value: tally.right,
                    of: tally.judged,
                  }))
                  .sort((a, b) => b.value / b.of - a.value / a.of)}
              />
            </ChartPanel>
          ) : null}
          {timed.length > 1 ? (
            <ChartPanel
              icon={Timer}
              title="Time per question"
              unit={
                run.totals?.median_seconds
                  ? `seconds, median ${Math.round(run.totals.median_seconds)}`
                  : "seconds"
              }
            >
              <ColumnChart
                unit="s"
                median={run.totals?.median_seconds ?? null}
                columns={timed.map((question) => ({
                  id: question.case,
                  value: question.seconds ?? 0,
                  detail: question.question,
                }))}
              />
            </ChartPanel>
          ) : null}
          {prompted.length ? (
            <ChartPanel
              icon={Eye}
              title="What the model could still see"
              unit="prompt size per model call, tokens"
            >
              <PromptChart
                questions={prompted.map((question) => ({
                  id: question.case,
                  name: question.question,
                  sizes: question.prompts,
                }))}
              />
            </ChartPanel>
          ) : null}
          {report.judged ? (
            <ChartPanel icon={ChartPie} title="Results" unit={`${report.judged} judged`}>
              <Outcomes
                headline={share(report.accuracy)}
                parts={[
                  {
                    name: "Right",
                    value: report.right,
                    color: "stroke-chart",
                    dot: "bg-chart",
                  },
                  {
                    name: "Wrong",
                    value: report.judged - report.right,
                    color: "stroke-chart-2",
                    dot: "bg-chart-2",
                  },
                  {
                    name: "Not played",
                    value: report.waiting,
                    color: "stroke-[url(#not-played)]",
                    dot: "bg-chart-soft",
                  },
                ]}
              />
            </ChartPanel>
          ) : null}
        </div>
      ) : null}
    </>
  );
}

/** Only a web address goes in a link; a stored run is data. */
function traceLink(url: string | null): string | null {
  return url && /^https?:\/\//i.test(url) ? url : null;
}

const GLYPHS = new Set(["pass", "flaky", "fail", "unjudged"]);

/** A question's result: a mark that differs by shape, and a word. */
function Status({ question }: { question: Question }) {
  return (
    <span className="flex min-w-0 items-center gap-1.5">
      {GLYPHS.has(question.verdict) ? (
        <VerdictGlyph verdict={question.verdict as Verdict} />
      ) : (
        <span className="mx-1 size-1.5 shrink-0 rounded-full bg-ink-3" aria-hidden />
      )}
      <span className="truncate" title={outcome(question).word}>
        {outcome(question).word}
      </span>
    </span>
  );
}

function kindOf(question: Question): string {
  return question.unanswerable ? "Unanswerable" : (KINDS[question.kind] ?? question.kind);
}

/** Worst first when sorted by result: what to read comes up top. */
const RESULT_ORDER = ["fail", "flaky", "unjudged", "pass"];
type Sort = "status" | "kind" | "tokens" | "seconds";
const SORTS: Record<Sort, (question: Question) => number | string> = {
  status: (question) => {
    const at = RESULT_ORDER.indexOf(question.verdict);
    return at < 0 ? RESULT_ORDER.length : at;
  },
  kind: kindOf,
  tokens: (question) => question.tokens ?? -1,
  seconds: (question) => question.seconds ?? -1,
};

function Head({
  label,
  sort,
  by,
  down,
  onSort,
  className,
}: {
  label: string;
  sort: Sort;
  by: Sort | null;
  down: boolean;
  onSort: (sort: Sort) => void;
  className: string;
}) {
  const Arrow = down ? ArrowDown : ArrowUp;
  return (
    <th
      scope="col"
      aria-sort={by === sort ? (down ? "descending" : "ascending") : undefined}
      className={cn("px-3 py-2 font-medium", className)}
    >
      <button
        type="button"
        onClick={() => onSort(sort)}
        className="inline-flex items-center gap-1 rounded hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
      >
        {label}
        {by === sort ? <Arrow className="size-3" aria-hidden /> : null}
      </button>
    </th>
  );
}

/** Every question, in play order until sorted. A column with nothing recorded isn't there. */
function QuestionTable({ rows, onOpen }: { rows: Question[]; onOpen: (id: string) => void }) {
  const [by, setBy] = useState<Sort | null>(null);
  const [down, setDown] = useState(false);
  const onSort = (sort: Sort) => {
    setDown(by === sort ? !down : sort === "tokens" || sort === "seconds");
    setBy(sort);
  };
  const sorted = by
    ? [...rows].sort((a, b) => {
        const [left, right] = [SORTS[by](a), SORTS[by](b)];
        const order =
          typeof left === "number" && typeof right === "number"
            ? left - right
            : String(left).localeCompare(String(right));
        return down ? -order : order;
      })
    : rows;
  const head = { by, down, onSort };
  const hasTokens = rows.some((question) => question.tokens);
  const hasSeconds = rows.some((question) => question.seconds);
  const hasTrace = rows.some((question) => question.verdict !== "not_run");
  return (
    <Group>
      <table className="w-full table-fixed border-collapse text-[13px]">
        <thead>
          <tr className="bg-head text-start text-[10.5px] tracking-[0.06em] text-ink-3 uppercase">
            <th scope="col" className="px-3 py-2 text-start font-medium">
              Question
            </th>
            <Head
              {...head}
              label="Result"
              sort="status"
              className="w-[204px] text-start max-md:hidden"
            />
            <Head
              {...head}
              label="Kind"
              sort="kind"
              className="w-[160px] text-start max-lg:hidden"
            />
            {hasTokens ? (
              <Head
                {...head}
                label="Tokens"
                sort="tokens"
                className="w-[84px] text-end max-md:hidden"
              />
            ) : null}
            {hasSeconds ? (
              <Head {...head} label="Time" sort="seconds" className="w-[72px] text-end" />
            ) : null}
            {hasTrace ? (
              <th scope="col" className="w-[52px] py-2 pe-3 text-end font-medium">
                <span className="max-md:sr-only">Trace</span>
              </th>
            ) : null}
          </tr>
        </thead>
        <tbody>
          {sorted.map((question) => (
            <QuestionRow
              key={question.case}
              question={question}
              hasTokens={hasTokens}
              hasSeconds={hasSeconds}
              hasTrace={hasTrace}
              onOpen={() => onOpen(question.case)}
            />
          ))}
        </tbody>
      </table>
    </Group>
  );
}

function QuestionRow({
  question,
  hasTokens,
  hasSeconds,
  hasTrace,
  onOpen,
}: {
  question: Question;
  hasTokens: boolean;
  hasSeconds: boolean;
  hasTrace: boolean;
  onOpen: () => void;
}) {
  const [open, setOpen] = useState(false);
  const trace = traceLink(question.trace_url);
  const more = [
    question.messages ? `${question.messages} messages` : null,
    question.usd ? usd(question.usd) : null,
    ...question.models,
  ].filter(Boolean);
  return (
    <>
      <tr className="border-t border-separator align-top hover:bg-selection">
        <td className="min-w-0 px-3 py-2">
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpen(!open)}
            className="flex w-full min-w-0 items-start gap-1.5 rounded text-start focus-visible:outline-2 focus-visible:outline-ring"
          >
            <ChevronRight
              className={cn(
                "mt-1 size-3 shrink-0 text-ink-3 transition-transform",
                open && "rotate-90",
              )}
              aria-hidden
            />
            <span className="min-w-0 flex-1">
              <span className="max-md:line-clamp-2 md:block md:truncate" title={question.question}>
                {question.question}
              </span>
              <span className="mt-0.5 block text-[12px] text-ink-2 md:hidden">
                <Status question={question} />
              </span>
            </span>
          </button>
        </td>
        <td className="px-3 py-2 max-md:hidden">
          <Status question={question} />
        </td>
        <td className="truncate px-3 py-2 text-ink-2 max-lg:hidden">{kindOf(question)}</td>
        {hasTokens ? (
          <td className="px-3 py-2 text-end text-ink-2 tabular-nums max-md:hidden">
            {question.tokens ? tokens(question.tokens) : null}
          </td>
        ) : null}
        {hasSeconds ? (
          <td className="px-3 py-2 text-end text-ink-2 tabular-nums">
            {question.seconds ? seconds(question.seconds) : null}
          </td>
        ) : null}
        {hasTrace ? (
          <td className="py-2 pe-3 text-end">
            {question.verdict !== "not_run" ? (
              <button
                type="button"
                onClick={onOpen}
                className="rounded font-semibold text-tint hover:underline focus-visible:outline-2 focus-visible:outline-ring"
              >
                View
              </button>
            ) : null}
          </td>
        ) : null}
      </tr>
      {open ? (
        <tr>
          <td colSpan={6} className="px-3 pb-3 ps-[30px]">
            <dl className="m-0 flex flex-col gap-2 text-[13.5px]">
              <Said label="Known answer" text={question.known || question.right_is} />
              {question.reply ? <Said label="It said" text={shortened(question.reply)} /> : null}
              {question.reason ? (
                <Said
                  label={question.tie_break ? "Verdict, by the tie-break" : "Verdict"}
                  text={`${outcome(question).word}. ${firstSentence(question.reason)}`}
                />
              ) : null}
              {question.error ? <Said label="What went wrong" text={question.error} /> : null}
              <dd className="m-0 flex flex-wrap items-center gap-3">
                <TextLink onClick={onOpen}>Every step, reply and trace</TextLink>
                {trace ? (
                  <a
                    href={trace}
                    target="_blank"
                    rel="noreferrer"
                    className="text-ink-2 underline decoration-ink-3 underline-offset-2 hover:text-foreground"
                  >
                    First trace in Phoenix
                  </a>
                ) : null}
              </dd>
              <Fold title="Details">
                <div className="flex flex-col gap-2 ps-5">
                  <Said label="Question" text={question.question} />
                  <Said label="What counts as right" text={question.right_is} />
                  {question.reply ? <Said label="The whole reply" text={question.reply} /> : null}
                  {question.reason ? (
                    <Said label="The judge's reason" text={question.reason} />
                  ) : null}
                  {more.length ? <Said label="Used" text={more.join(" · ")} /> : null}
                </div>
              </Fold>
            </dl>
          </td>
        </tr>
      ) : null}
    </>
  );
}

/** The first sentence of a reason, as far as a line goes. */
function firstSentence(text: string): string {
  const first = text.split(/(?<=[.!?])\s/)[0] ?? text;
  return first.length > SHORT_CHARS ? `${first.slice(0, SHORT_CHARS).trimEnd()}…` : first;
}

function shortened(text: string): string {
  return text.length > REPLY_CHARS ? `${text.slice(0, REPLY_CHARS).trimEnd()}…` : text;
}

function Said({ label, text }: { label: string; text: string }) {
  return (
    <div>
      <dt className="text-[12px] font-semibold text-ink-2">{label}</dt>
      <dd className="m-0 whitespace-pre-wrap [overflow-wrap:anywhere]">{text}</dd>
    </div>
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
  return (
    <section>
      <GroupHeading title="Runs" />
      <Group>
        {listed.map((run) => (
          <button
            key={run.run}
            type="button"
            aria-current={run.run === selected.run ? "true" : undefined}
            onClick={() => onPick(run.run)}
            className={cn(
              "flex w-full flex-wrap items-center gap-x-3 gap-y-0.5 border-t border-separator px-4 py-2 text-start text-[13px] first:border-t-0 hover:bg-selection focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring",
              run.run === selected.run && "bg-selection-strong",
            )}
          >
            {run.report.accuracy === null ? null : (
              <span className="font-semibold tabular-nums">{share(run.report.accuracy)}</span>
            )}
            {run.report.judged ? (
              <span className="text-ink-2 tabular-nums">
                {run.report.right} of {run.report.judged}
              </span>
            ) : null}
            <span className="min-w-0 text-ink-2">{benchmarkName(run.set)}</span>
            {run.status === "running" ? <span className="text-ink-2">Running</span> : null}
            {didntFinish(run) ? <span className="text-ink-3">Didn't finish</span> : null}
            <span className="ms-auto text-[12px] text-ink-3 tabular-nums">
              {run.started_at ? when(run.started_at) : null}
            </span>
          </button>
        ))}
      </Group>
    </section>
  );
}

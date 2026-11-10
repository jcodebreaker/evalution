import { useState } from "react";
import { api, errorText } from "../../lib/api";
import { cn } from "../../lib/cn";
import { seconds, tokens, when } from "../../lib/format";
import type { Benchmarks } from "../../lib/schema";
import { Button } from "../../ui/button";
import { TextLink } from "../shared/group";

export type Run = Benchmarks["runs"][number];
type MetricKey =
  | "accuracy"
  | "average"
  | "recall"
  | "reached"
  | "compacted"
  | "abstention"
  | "retrieval";
type Metric = {
  key: MetricKey;
  label: string;
  value: number | null;
  note: string;
  /** Where the true share lies, 95% of the time. */
  range?: [number, number] | null;
};

export function share(value: number | null): string {
  return value === null ? "–" : `${Math.round(value * 100)}%`;
}

/** A 95% range in words: `95% range 76–99%`. */
export function rangeText(range: [number, number]): string {
  return `95% range ${Math.round(range[0] * 100)}–${share(range[1])}`;
}

/** Questions that got as far as a verdict, right or not. */
export function played(run: Run): number {
  return run.questions.length - run.report.waiting;
}

function howCalculated(key: MetricKey, run: Run): { lines: string; who: string } {
  const { report } = run;
  const judge = run.judge ?? "The grader";
  const kinds = Object.values(report.kinds);
  const yesNo = `${judge} says yes or no against the known answer. Code does the arithmetic.`;
  if (key === "accuracy") {
    return {
      lines: `questions right / questions judged\n= ${report.right} / ${report.judged} = ${share(report.accuracy)}${report.range ? `\n\n${rangeText(report.range)} (Wilson)` : ""}${report.broke_off ? `\nthe app broke off on ${report.broke_off}, counted as wrong` : ""}`,
      who: `${yesNo} Unanswerable questions count: right when it said it didn't know.`,
    };
  }
  if (key === "average") {
    const each = kinds.map((tally) => `${tally.right}/${tally.judged}`).join(" + ");
    return {
      lines: `mean of each kind's accuracy\n= (${each}) / ${kinds.length} = ${share(report.task_average)}`,
      who: `${yesNo} Each kind counts the same, however many questions it has.`,
    };
  }
  if (key === "recall") {
    const { memory } = report;
    return {
      lines: `golden answers in committed memory / questions measured\n= ${memory.committed} / ${memory.measured} = ${share(report.write_recall)}\n\nwritten, committed or not = ${memory.written}`,
      who: `Memory is read just before the question. ${judge} says whether it holds the golden answer, and never decides the question. Questions where memory couldn't be read aren't counted.`,
    };
  }
  if (key === "reached") {
    const { memory } = report;
    return {
      lines: `golden answers in what the model was sent / questions measured\n= ${memory.reached} / ${memory.reach_measured} = ${share(report.reached)}\n\nmessages compacted by then: ${memory.compacted} of ${memory.compact_measured}`,
      who: `Read from the traces of the call that answered. ${judge} says whether it holds the golden answer. Without traces this isn't measured.`,
    };
  }
  if (key === "compacted") {
    const { memory } = report;
    return {
      lines: `questions whose prompt really shrank / questions measured\n= ${memory.compacted} / ${memory.compact_measured}`,
      who: "Read from the size of each prompt. Where nothing folded, the raw chat was still there, so the score doesn't test memory.",
    };
  }
  if (key === "abstention") {
    return {
      lines: `said it didn't know / unanswerable questions\n= ${report.admitted} / ${report.unanswerable} = ${share(report.abstention)}`,
      who: "These questions have no answer in the history. An agent that remembers nothing scores well here, so read this beside accuracy.",
    };
  }
  return {
    lines:
      "recall_any@k = 1 if any evidence is in the top k\nrecall_all@k = 1 if all evidence is in the top k\nnDCG@k = DCG@k / ideal DCG@k",
    who: "Needs an adapter that shows what the agent retrieved, in order. None does yet.",
  };
}

/** A benchmark set in words: `LongMemEval quick`, `LongMemEval S · One conversation`. */
export function benchmarkName(set: string): string {
  const name = set.replace("longmemeval-", "");
  const size = name.replace("-same", "");
  const word: Record<string, string> = { quick: "quick", oracle: "full", s: "S", m: "M" };
  return `LongMemEval ${word[size] ?? size}${name === size ? "" : " · One conversation"}`;
}

/** Each measured number of a run. What wasn't measured isn't here: no data is not 0%. */
function metrics(report: Run["report"]): Metric[] {
  const { memory } = report;
  const all: Metric[] = [
    {
      key: "accuracy",
      label: "Answer accuracy",
      value: report.accuracy,
      note: `${report.right} of ${report.judged}`,
      range: report.range,
    },
    {
      key: "recall",
      label: "Memory write recall",
      value: report.write_recall,
      note: `${memory.committed} of ${memory.measured}`,
    },
    {
      key: "reached",
      label: "Reached the model",
      value: report.reached,
      note: `${memory.reached} of ${memory.reach_measured}`,
    },
    {
      key: "compacted",
      label: "Questions that folded",
      value: memory.compact_measured ? memory.compacted / memory.compact_measured : null,
      note: `${memory.compacted} of ${memory.compact_measured}`,
    },
    {
      key: "abstention",
      label: "Abstention",
      value: report.abstention,
      note: `${report.admitted} of ${report.unanswerable}`,
    },
  ];
  return all.filter((metric) => metric.value !== null);
}

/** Points gained since the run before, on the rounded numbers the cards show, so the sum agrees.
 * Only accuracy has a range, and a move inside it isn't told from chance, so only a real one shows. */
function change(key: MetricKey, report: Run["report"]): number | null {
  const { accuracy, previous_accuracy: before } = report;
  if (key !== "accuracy" || !report.change_is_real || accuracy === null || before === null) {
    return null;
  }
  return Math.round(accuracy * 100) - Math.round(before * 100);
}

/** The run at a glance: what happened, how far it has got, and its numbers against the run before. */
export function Glance({ run, onStopped }: { run: Run; onStopped: () => Promise<void> | void }) {
  const [open, setOpen] = useState<MetricKey | null>(null);
  const { report } = run;
  const pick = (key: MetricKey) => setOpen(open === key ? null : key);
  const cards = metrics(report);
  const how = open ? howCalculated(open, run) : null;
  const running = run.status === "running";
  const didntFinish = run.status === "stopped" || run.status === "failed";
  return (
    <>
      <div className="flex flex-col gap-0.5 px-1">
        <p className="m-0 flex flex-wrap gap-x-2 text-[14px] text-ink-2">
          {benchmarkName(run.set)}
          {run.started_at ? <span className="text-ink-3">{when(run.started_at)}</span> : null}
          {didntFinish ? <span className="font-semibold text-warning">Didn't finish</span> : null}
        </p>
        <Usage run={run} />
      </div>
      {run.happened ? (
        <p
          className={cn(
            "glass m-0 rounded-xl px-4 py-3 text-[14px] font-semibold",
            run.untested && "text-warning",
          )}
        >
          {run.happened}
        </p>
      ) : null}
      {running ? <Progress run={run} onStopped={onStopped} big={!cards.length} /> : null}
      {cards.length ? (
        <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,150px),1fr))] gap-3">
          {cards.map((card) => {
            const delta = change(card.key, report);
            return (
              <button
                key={card.key}
                type="button"
                aria-expanded={open === card.key}
                onClick={() => pick(card.key)}
                className={cn(
                  "glass flex min-w-0 flex-col gap-2 rounded-[12px] p-4 text-start hover:border-rule-strong focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-tint",
                  open === card.key && "border-tint ring-3 ring-tint-soft-2",
                )}
              >
                <span className="truncate text-[13px] text-ink-2">{card.label}</span>
                <span className="flex flex-wrap items-center gap-2">
                  <span className="text-[24px] leading-none font-semibold tracking-[-0.02em] tabular-nums">
                    {share(card.value)}
                  </span>
                  {delta ? (
                    <span
                      className={cn(
                        "rounded-full px-1.5 py-px text-[12px] font-medium tabular-nums",
                        delta > 0 ? "bg-pass-soft text-pass" : "bg-fail-soft text-fail",
                      )}
                    >
                      {delta > 0 ? "+" : "−"}
                      {Math.abs(delta)} pts
                    </span>
                  ) : null}
                </span>
                <span className="text-[12.5px] text-ink-3 tabular-nums">{card.note}</span>
                {card.range ? (
                  <span className="text-[12.5px] text-ink-3 tabular-nums">
                    {rangeText(card.range)}
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>
      ) : null}
      {cards.length ? (
        <p className="m-0 flex flex-wrap gap-x-3 px-1 text-[12.5px] text-ink-3">
          {report.broke_off ? (
            <span className="font-semibold text-warning tabular-nums">
              {report.broke_off} timed out, counted as wrong
            </span>
          ) : null}
          <span>A sample of {report.judged} questions, not comparable with published scores.</span>
        </p>
      ) : null}
      {how ? (
        <div className="glass flex flex-col gap-1.5 rounded-xl px-5 py-3.5">
          <pre
            title={how.who}
            className="m-0 font-mono text-[14px] leading-relaxed whitespace-pre-wrap [overflow-wrap:anywhere]"
          >
            {how.lines}
          </pre>
          <div className="flex flex-wrap gap-x-3 text-[12.5px]">
            {report.task_average === null || open === "average" ? null : (
              <TextLink onClick={() => pick("average")}>
                Task average {share(report.task_average)}
              </TextLink>
            )}
            {open === "retrieval" ? null : (
              <TextLink onClick={() => pick("retrieval")}>Retrieval recall</TextLink>
            )}
          </div>
        </div>
      ) : null}
    </>
  );
}

/** How far a going run has got. The question being played counts by its messages, so a long one still moves. */
function Progress({
  run,
  onStopped,
  big,
}: {
  run: Run;
  onStopped: () => Promise<void> | void;
  /** Nothing else to show yet, so this is the screen. */
  big: boolean;
}) {
  const [stopping, setStopping] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);
  const stop = async () => {
    setStopping(true);
    setFailed(null);
    try {
      await api.stop(run.run);
      await onStopped();
    } catch (error) {
      setFailed(errorText(error, "Couldn't stop it. Try again."));
    } finally {
      setStopping(false);
    }
  };
  const total = run.questions.length;
  const done = played(run);
  const { current } = run;
  const within = current?.turns ? (current.turn ?? 0) / current.turns : 0;
  const part = total ? Math.min((done + within) / total, 1) : 0;
  const step =
    current?.stage === "grading"
      ? "Judging an answer"
      : current?.turns
        ? (current.turn ?? 0) >= current.turns
          ? "Asking the question"
          : `Sending this question's chat history, message ${current.turn ?? 0} of ${current.turns}`
        : current?.name;
  return (
    <div className={cn("glass flex flex-col gap-2.5 rounded-xl px-5 py-4", big && "gap-3 py-6")}>
      <div className="flex items-center gap-3">
        <span
          className={cn(
            "font-display font-semibold tabular-nums",
            big ? "text-[36px] leading-tight tracking-[-0.03em]" : "text-[15px]",
          )}
        >
          {done} of {total} questions
        </span>
        <Button
          size="sm"
          variant="secondary"
          className="ms-auto"
          disabled={stopping}
          onClick={() => void stop()}
        >
          {stopping ? "Stopping" : "Stop"}
        </Button>
      </div>
      <div
        role="progressbar"
        aria-label="Questions played"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={done}
        className="h-1.5 overflow-hidden rounded-full bg-muted"
      >
        <div className="h-full rounded-full bg-chart" style={{ width: `${part * 100}%` }} />
      </div>
      {step ? <p className="m-0 truncate text-[13px] text-ink-2 tabular-nums">{step}</p> : null}
      {failed ? <p className="m-0 text-[14px] text-fail">{failed}</p> : null}
    </div>
  );
}

function compactTokens(value: number): string {
  return value < 1_000_000 ? tokens(value) : `${(value / 1_000_000).toFixed(1)}M`;
}

/** What the run came to, on one quiet line. A figure that wasn't recorded isn't shown. */
function Usage({ run }: { run: Run }) {
  const { totals } = run;
  const parts = [
    run.status === "running" ? null : `${played(run)} of ${run.questions.length} played`,
    totals?.tokens ? `${compactTokens(totals.tokens)} tokens` : null,
    totals?.median_seconds ? `${seconds(totals.median_seconds)} median` : null,
    totals?.models.length ? totals.models.join(", ") : null,
    run.judge ? `judged by ${run.judge}` : null,
  ].filter(Boolean);
  if (!parts.length) return null;
  return (
    <p className="m-0 text-[12.5px] text-ink-3 tabular-nums [overflow-wrap:anywhere]">
      {parts.join(" · ")}
    </p>
  );
}

import { useState } from "react";
import { api, errorText } from "../../lib/api";
import { cn } from "../../lib/cn";
import { seconds, tokens, usd } from "../../lib/format";
import type { Benchmarks } from "../../lib/schema";
import { Button } from "../../ui/button";
import { Group } from "../shared/group";

export type Run = Benchmarks["runs"][number];
type MetricKey = "accuracy" | "average" | "recall" | "abstention" | "retrieval";

export function share(value: number | null): string {
  return value === null ? "–" : `${Math.round(value * 100)}%`;
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
      lines: `questions right / questions judged\n= ${report.right} / ${report.judged} = ${share(report.accuracy)}`,
      who: yesNo,
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

const STROKE = 13;
const GAP = 3;
const SIZE = 148;

/** Concentric rings, outermost first. No value leaves the track bare: no data is not 0%, and the numbers beside them say which it is. */
function Rings({
  rings,
  open,
  onPick,
}: {
  rings: { key: MetricKey; label: string; value: number | null; tone: string }[];
  open: MetricKey | null;
  onPick: (key: MetricKey) => void;
}) {
  return (
    <svg
      viewBox={`0 0 ${SIZE} ${SIZE}`}
      width={SIZE}
      height={SIZE}
      className="shrink-0"
      role="img"
      aria-label={rings.map((ring) => `${ring.label} ${share(ring.value)}`).join(", ")}
    >
      {rings.map((ring, index) => {
        const radius = SIZE / 2 - STROKE / 2 - index * (STROKE + GAP);
        const around = 2 * Math.PI * radius;
        // The round caps add one stroke's width, so the arc is cut by that to stay to scale.
        const arc = Math.max((ring.value ?? 0) * around - STROKE, 0.01);
        return (
          // biome-ignore lint/a11y/noStaticElementInteractions: a pointer shortcut; the number beside each ring is the keyboard way to the same formula.
          <g
            key={ring.key}
            fill="none"
            strokeWidth={STROKE}
            onClick={() => onPick(ring.key)}
            className={cn("transition-opacity", open && open !== ring.key && "opacity-35")}
          >
            <title>{`${ring.label} ${share(ring.value)}`}</title>
            <circle
              cx={SIZE / 2}
              cy={SIZE / 2}
              r={radius}
              stroke="currentColor"
              className="text-muted"
            />
            {ring.value ? (
              <circle
                cx={SIZE / 2}
                cy={SIZE / 2}
                r={radius}
                stroke="currentColor"
                strokeLinecap="round"
                strokeDasharray={`${arc} ${around}`}
                strokeDashoffset={-STROKE / 2}
                transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}
                className={ring.tone}
              />
            ) : null}
          </g>
        );
      })}
    </svg>
  );
}

/** The run at a glance: its rings, the numbers beside them, what happened, and how far it has got. */
export function Glance({ run, onStopped }: { run: Run; onStopped: () => Promise<void> | void }) {
  const [open, setOpen] = useState<MetricKey | null>(null);
  const { report } = run;
  const pick = (key: MetricKey) => setOpen(open === key ? null : key);
  const rings = [
    {
      key: "accuracy" as const,
      label: "Answer accuracy",
      value: report.accuracy,
      note: report.judged ? `${report.right} of ${report.judged}` : "",
      tone: "text-tint",
    },
    {
      key: "recall" as const,
      label: "Memory write recall",
      value: report.write_recall,
      note: report.memory.measured
        ? `${report.memory.committed} of ${report.memory.measured}`
        : played(run)
          ? "Not measured"
          : "",
      tone: "text-foreground",
    },
    {
      key: "abstention" as const,
      label: "Abstention",
      value: report.abstention,
      note: report.unanswerable ? `${report.admitted} of ${report.unanswerable}` : "",
      tone: "text-mid",
    },
  ];
  const how = open ? howCalculated(open, run) : null;
  const small = (key: MetricKey, text: string) => (
    <button
      type="button"
      aria-expanded={open === key}
      onClick={() => pick(key)}
      className={cn(
        "rounded-md px-1.5 py-0.5 hover:bg-selection focus-visible:outline-2 focus-visible:outline-ring",
        open === key && "bg-selection-strong",
      )}
    >
      {text}
    </button>
  );
  return (
    <Group>
      {run.status === "running" ? <Progress run={run} onStopped={onStopped} /> : null}
      <div className="flex flex-wrap items-center justify-center gap-x-8 gap-y-4 px-5 py-5 max-md:px-3">
        <Rings rings={rings} open={open} onPick={pick} />
        <div className="flex min-w-0 flex-1 basis-80 flex-col gap-3">
          {run.happened ? (
            <p className="m-0 px-1.5 font-display text-[16px] font-bold tracking-[-0.2px]">
              {run.happened}
            </p>
          ) : null}
          <div className="grid grid-cols-3 gap-1">
            {rings.map((ring) => (
              <button
                key={ring.key}
                type="button"
                aria-expanded={open === ring.key}
                onClick={() => pick(ring.key)}
                className={cn(
                  "flex min-w-0 flex-col gap-1 rounded-lg px-1.5 py-1.5 text-start hover:bg-selection focus-visible:outline-2 focus-visible:outline-ring",
                  open === ring.key && "bg-selection-strong",
                )}
              >
                <span className="font-display text-[30px] leading-none font-extrabold tabular-nums">
                  {share(ring.value)}
                </span>
                <span className="flex items-baseline gap-1.5 text-[12px] leading-tight text-ink-2">
                  <span
                    className={cn("size-2 shrink-0 rounded-full bg-current", ring.tone)}
                    aria-hidden
                  />
                  {ring.label}
                </span>
                {ring.note ? (
                  <span className="text-[12px] text-ink-3 tabular-nums">{ring.note}</span>
                ) : null}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap gap-x-2 text-[12px] text-ink-3">
            {report.task_average === null
              ? null
              : small("average", `Task average ${share(report.task_average)}`)}
            {small("retrieval", "Retrieval recall: not available yet")}
          </div>
        </div>
      </div>
      {how ? (
        <div className="flex flex-col gap-2 border-t border-separator bg-muted px-5 py-3">
          <pre className="m-0 font-mono text-[13px] leading-relaxed whitespace-pre-wrap [overflow-wrap:anywhere]">
            {how.lines}
          </pre>
          <p className="m-0 text-[13px] text-ink-2">{how.who}</p>
        </div>
      ) : null}
    </Group>
  );
}

/** How far a going run has got. The question being played counts by its messages, so a long one still moves. */
function Progress({ run, onStopped }: { run: Run; onStopped: () => Promise<void> | void }) {
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
      ? "Judging"
      : current?.turns
        ? `Message ${current.turn ?? 0} of ${current.turns}`
        : "";
  return (
    <div className="flex flex-col gap-2 border-b border-separator px-5 py-3.5 max-md:px-3">
      <div className="flex items-center gap-3">
        <span className="font-display text-[20px] font-extrabold tabular-nums">
          {done} of {total}
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
        className="h-2 overflow-hidden rounded-full bg-muted"
      >
        <div className="h-full rounded-full bg-tint" style={{ width: `${part * 100}%` }} />
      </div>
      {step || current?.name ? (
        <p className="m-0 truncate text-[12px] text-ink-2">
          {[step, current?.name].filter(Boolean).join(" · ")}
        </p>
      ) : null}
      {failed ? <p className="m-0 text-[13px] text-destructive">{failed}</p> : null}
    </div>
  );
}

function compactTokens(value: number): string {
  return value < 1_000_000 ? tokens(value) : `${(value / 1_000_000).toFixed(1)}M`;
}

/** What the run came to. A figure that wasn't recorded isn't shown. */
export function StatStrip({ run }: { run: Run }) {
  const { totals } = run;
  const models = totals?.models.join(", ");
  const numbers: [string, string | null][] = [
    ["Played", run.status === "running" ? null : `${played(run)} of ${run.questions.length}`],
    ["Tokens", totals?.tokens ? compactTokens(totals.tokens) : null],
    ["Spent", totals?.usd ? usd(totals.usd) : null],
    ["Median", totals?.median_seconds ? seconds(totals.median_seconds) : null],
  ];
  const names: [string, string | null | undefined][] = [
    ["Model", models],
    ["Judge", run.judge],
  ];
  return (
    <Group className="flex flex-wrap items-baseline gap-x-7 gap-y-3 px-5 py-3.5 max-md:px-3">
      {numbers.map(([label, value]) =>
        value ? (
          <div key={label} className="min-w-0">
            <div className="text-[11.5px] text-ink-2">{label}</div>
            <div className="font-display text-[20px] leading-tight font-bold tabular-nums">
              {value}
            </div>
          </div>
        ) : null,
      )}
      {names.map(([label, value]) =>
        value ? (
          <div key={label} className="min-w-0">
            <div className="text-[11.5px] text-ink-2">{label}</div>
            <div className="text-[13px] font-semibold [overflow-wrap:anywhere]">{value}</div>
          </div>
        ) : null,
      )}
    </Group>
  );
}

/** Accuracy across runs, oldest to newest, on a fixed 0 to 100% scale. */
export function Trend({ points }: { points: { at: string; value: number }[] }) {
  const width = 96;
  const height = 24;
  const x = (index: number) => 3 + (index * (width - 6)) / (points.length - 1);
  const y = (value: number) => height - 3 - value * (height - 6);
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      role="img"
      aria-label={`Answer accuracy across runs: ${points.map((point) => share(point.value)).join(", ")}`}
    >
      <line
        x1={0}
        x2={width}
        y1={height - 3}
        y2={height - 3}
        stroke="currentColor"
        className="text-separator"
      />
      <polyline
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinejoin="round"
        className="text-tint"
        points={points.map((point, index) => `${x(index)},${y(point.value)}`).join(" ")}
      />
      {points.map((point, index) => (
        <circle
          key={point.at}
          cx={x(index)}
          cy={y(point.value)}
          r={index === points.length - 1 ? 3 : 1.5}
          fill="currentColor"
          className="text-tint"
        >
          <title>{`${point.at}: ${share(point.value)}`}</title>
        </circle>
      ))}
    </svg>
  );
}

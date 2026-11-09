import { ArrowRight } from "lucide-react";
import { cn } from "../../lib/cn";
import type { Compare, LaneStep, ToolRow } from "../../lib/schema";
import { SectionHead } from "./SectionHead";

const NODE: Record<LaneStep["status"] | "plan", string> = {
  plan: "bg-bubble",
  ok: "bg-bubble",
  missing: "border-[1.5px] border-dashed border-fail text-fail",
  extra: "ring-1 ring-inset ring-separator",
  wrong: "bg-fail-soft text-fail",
  unjudged: "ring-1 ring-inset ring-ink-3 text-ink-2",
  automatic: "text-ink-3 ring-1 ring-inset ring-separator",
};

function Lane({
  steps,
  marked = -1,
}: {
  steps: { label: string; status: LaneStep["status"] | "plan" }[];
  /** The step where this try first differs from a passing one. */
  marked?: number;
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {steps.map((step, index) => (
        <span key={index} className="contents">
          {index ? <ArrowRight className="size-3 text-ink-3" aria-hidden /> : null}
          <span
            className={cn(
              "inline-flex h-7 items-center rounded-lg px-2.5 text-[13px]",
              NODE[step.status],
              index === marked && "outline-2 outline-offset-1 outline-foreground",
            )}
            title={
              index === marked
                ? DIFFERS
                : step.status === "automatic"
                  ? "Ran by itself, not scored"
                  : step.status === "plan" || step.status === "ok"
                    ? undefined
                    : step.status
            }
          >
            {step.label}
            {step.status === "automatic" ? " · automatic" : ""}
          </span>
        </span>
      ))}
    </div>
  );
}

/** Each expected call beside the call made, in the shown try. */
function ToolRows({ rows, try_ }: { rows: ToolRow[]; try_: number | null }) {
  return (
    <div className="group-card grid grid-cols-2 text-[13px] max-md:grid-cols-1">
      <h4 className="m-0 px-3.5 pt-2.5 pb-1 text-[12px] font-medium text-ink-3 max-md:hidden">
        Expected
      </h4>
      <h4 className="m-0 px-3.5 pt-2.5 pb-1 text-[12px] font-medium text-ink-3 max-md:hidden">
        What it did{try_ ? ` (try ${try_})` : ""}
      </h4>
      {rows.map((row, index) => (
        <div
          key={index}
          className="col-span-2 grid grid-cols-subgrid border-t border-separator max-md:col-span-1 max-md:grid-cols-1"
        >
          <Call label="Expected" text={row.expected} />
          <div className="min-w-0">
            <Call label="What it did" text={row.actual} />
            <p
              className={cn(
                "m-0 px-3.5 pb-2 -mt-1 text-[12.5px]",
                row.status === "matched" || row.status === "extra" || row.status === "automatic"
                  ? "text-ink-3"
                  : "text-fail",
              )}
            >
              {row.status === "automatic" ? "Ran by itself, not scored" : row.reason || row.status}
            </p>
          </div>
        </div>
      ))}
    </div>
  );
}

function Call({ label, text }: { label: string; text: string | null }) {
  return (
    <div className="min-w-0 px-3.5 py-2">
      <span className="block text-[11px] text-ink-3 md:hidden">{label}</span>
      <code className="font-mono text-[12.5px] break-words">{text ?? "—"}</code>
    </div>
  );
}

const DIFFERS = "Differs from passing try";

/** The node of *steps* where the failing try first differs: its call, else its reply (the last). */
function divergedAt(steps: LaneStep[], span: string | null): number {
  const found = span ? steps.findIndex((step) => step.span === span) : -1;
  return found >= 0 ? found : steps.length - 1;
}

/**
 * What it should do against what each try did: the shown try first. Where a failing try first
 * differs from a passing one is marked, with a way to that passing try.
 */
export function Trajectory({
  shown,
  runLabel,
  onPassing,
}: {
  shown: Compare;
  runLabel: (run: string) => string;
  /** Shows the passing try, at the step where they differ. */
  onPassing: (run: string, n: number, span: string | null) => void;
}) {
  const trajectory = shown.trajectory;
  if (!trajectory) return null;
  const lanes = [...trajectory.tries].sort(
    (a, b) => Number(b.n === shown.try) - Number(a.n === shown.try) || a.n - b.n,
  );
  const diverged = shown.divergence?.fail.run === shown.run ? shown.divergence : null;
  return (
    <section className="flex flex-col gap-2">
      <SectionHead title="Trajectory" sub="what it should do vs what it did" />
      <div className="group-card grid grid-cols-[72px_minmax(0,1fr)] items-center gap-x-3 gap-y-2 p-3.5">
        <span className="text-[12.5px] font-semibold text-ink-3">Expected</span>
        <Lane
          steps={trajectory.expected.map((label) => ({
            label,
            status: "plan" as const,
          }))}
        />
        {lanes.map((lane) => (
          <span key={lane.n} className="contents">
            <span
              className={cn(
                "text-[12.5px] font-semibold",
                lane.n === shown.try ? "text-foreground" : "text-ink-3",
              )}
            >
              Try {lane.n}
            </span>
            <Lane
              steps={lane.steps}
              marked={
                diverged && lane.n === diverged.fail.n
                  ? divergedAt(lane.steps, diverged.fail.span)
                  : -1
              }
            />
          </span>
        ))}
      </div>
      {diverged ? (
        <p className="m-0 px-1 text-[12.5px] text-ink-3">
          <button
            type="button"
            className="underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-ring"
            onClick={() => onPassing(diverged.pass.run, diverged.pass.n, diverged.pass.span)}
          >
            {DIFFERS} {diverged.pass.n}
            {diverged.pass.run === shown.run ? "" : ` (${runLabel(diverged.pass.run)})`}
          </button>
          : {diverged.detail}
        </p>
      ) : null}
      {lanes.some((lane) => lane.steps.some((step) => step.status === "automatic")) ? (
        <p className="m-0 px-1 text-[12.5px] text-ink-3">Automatic: ran by itself, not scored</p>
      ) : null}
      {shown.tool_rows.length ? <ToolRows rows={shown.tool_rows} try_={shown.try} /> : null}
    </section>
  );
}

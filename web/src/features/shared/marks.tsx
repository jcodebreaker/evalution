import { cn } from "../../lib/cn";
import { score } from "../../lib/format";
import type { Suspect, TryOutcome, Verdict } from "../../lib/schema";
import { verdictWord } from "../../lib/verdict";
import type { Outcome } from "../../ui/status";
import { StatusDot } from "../../ui/status";

/** A verdict as the one outcome mark (ui/status.tsx): a dot whose colour carries it, half pass and
 * half fail for flaky. Never a tick or a cross. */
export function VerdictGlyph({ verdict, className }: { verdict: Verdict; className?: string }) {
  return (
    <StatusDot
      state={DOT[verdict]}
      label={verdictWord(verdict)}
      className={cn(
        verdict === "flaky" && "bg-[linear-gradient(90deg,var(--pass)_50%,var(--fail)_50%)]",
        className,
      )}
    />
  );
}

const DOT: Record<Verdict, Outcome> = {
  pass: "pass",
  flaky: "fail",
  fail: "fail",
  unjudged: "unjudged",
};

/** A test with no result yet: the hollow dot. */
export function NotRunGlyph({ className }: { className?: string }) {
  return <StatusDot state="none" className={className} />;
}

/** A 0–1 score with its bar; both turn destructive below the pass score. */
export function ScoreBar({ value, pass }: { value: number | null; pass: number }) {
  if (value === null) return <span className="text-ink-3">—</span>;
  const low = value < pass;
  return (
    <div className="flex min-w-0 items-center gap-2 text-[12.5px]">
      <span className={cn("w-[30px] text-end font-mono tabular-nums", low && "text-fail")}>
        {score(value)}
      </span>
      <span className="h-1.5 min-w-7 flex-1 overflow-hidden rounded-full bg-sunk-2">
        <span
          className={cn("block h-full rounded-full", low ? "bg-fail" : "bg-ink-3")}
          style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }}
        />
      </span>
    </div>
  );
}

const TRY_DOT: Record<TryOutcome, string> = {
  passed: "bg-pass",
  failed: "bg-fail",
  unjudged: "bg-unjudged",
};

/** One dot per try, in the outcome colours: passed, failed, couldn't judge. */
export function TryDots({ tries }: { tries: readonly TryOutcome[] }) {
  const failed = tries.filter((outcome) => outcome === "failed").length;
  return (
    <span
      role="img"
      aria-label={`${failed} of ${tries.length} failed`}
      className="inline-flex gap-[3px] align-middle"
    >
      {tries.map((outcome, index) => (
        <i key={index} className={cn("size-2 rounded-full", TRY_DOT[outcome])} />
      ))}
    </span>
  );
}

const SUSPECT_LABEL: Record<Suspect, string> = {
  app: "App?",
  test: "Test?",
  unclear: "Unclear",
};

/** Where the model reads the fault to be, as a small soft badge. */
export function SuspectTag({ suspect }: { suspect: Suspect }) {
  return (
    <span className="inline-flex h-5 shrink-0 items-center rounded-[5px] bg-sunk-2 px-[7px] text-[11px] font-medium text-ink-2">
      {SUSPECT_LABEL[suspect]}
    </span>
  );
}

/** A test that may be out of date with its app, and why on hover. */
export function StaleChip({ reason }: { reason: string }) {
  return (
    <span
      title={reason}
      className="inline-flex h-5 shrink-0 items-center rounded-[5px] bg-unjudged-soft px-[7px] text-[11px] font-medium text-warning"
    >
      May be stale
    </span>
  );
}

/** How a test plays: through the app's page, as a person would, or through its API. */
export function PlaysChip({ plays }: { plays: "api" | "page" }) {
  const page = plays === "page";
  return (
    <span
      title={
        page
          ? "Played through the app's page: types each message, checks it shows"
          : "Played through the app's API"
      }
      data-testid="plays-chip"
      className={cn(
        "inline-flex h-5 shrink-0 items-center rounded-[5px] px-[7px] text-[11px] font-semibold tracking-[0.01em]",
        page ? "bg-tint-soft text-tint-ink" : "bg-sunk-2 text-ink-2",
      )}
    >
      {page ? "Page" : "API"}
    </span>
  );
}

/** A verdict as a cell's fill, in the outcome colours. */
export const VERDICT_CELL: Record<Verdict, string> = {
  pass: "bg-pass",
  flaky: "bg-[linear-gradient(90deg,var(--pass)_50%,var(--fail)_50%)]",
  fail: "bg-fail",
  unjudged: "bg-unjudged",
};

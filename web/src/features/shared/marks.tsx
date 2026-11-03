import { cn } from "../../lib/cn";
import { score } from "../../lib/format";
import type { Suspect, TryOutcome, Verdict } from "../../lib/schema";
import { verdictWord } from "../../lib/verdict";

/** A verdict: a quiet check, a half-filled ring for flaky, a filled cross for fail, a question
 * mark in a ring when nobody could judge. */
export function VerdictGlyph({ verdict, className }: { verdict: Verdict; className?: string }) {
  return (
    <svg
      viewBox="0 0 16 16"
      role="img"
      aria-label={verdictWord(verdict)}
      className={cn(
        "size-3.5 shrink-0",
        verdict === "pass" && "text-success",
        verdict === "flaky" && "text-warning",
        verdict === "fail" && "text-destructive",
        verdict === "unjudged" && "text-ink-2",
        className,
      )}
    >
      {verdict === "pass" ? (
        <path
          d="M3.5 8.5l3 3 6-7"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ) : verdict === "unjudged" ? (
        <>
          <circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" strokeWidth="1.6" />
          <path
            d="M6.3 6.4a1.8 1.8 0 1 1 2.6 1.6c-.6.3-.9.7-.9 1.3"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.4"
            strokeLinecap="round"
          />
          <circle cx="8" cy="11.4" r="0.8" fill="currentColor" />
        </>
      ) : verdict === "flaky" ? (
        <>
          <circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" strokeWidth="1.6" />
          <path d="M8 1.8a6.2 6.2 0 0 1 0 12.4z" fill="currentColor" />
        </>
      ) : (
        <>
          <circle cx="8" cy="8" r="7" fill="currentColor" />
          <path
            d="M5.5 5.5l5 5M10.5 5.5l-5 5"
            className="stroke-background"
            strokeWidth="1.6"
            strokeLinecap="round"
          />
        </>
      )}
    </svg>
  );
}

/** A test with no result yet: a dashed ring. */
export function NotRunGlyph({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 16 16"
      role="img"
      aria-label="Not run"
      className={cn("size-3.5 shrink-0 text-ink-3", className)}
    >
      <circle
        cx="8"
        cy="8"
        r="6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeDasharray="2 2"
      />
    </svg>
  );
}

/** A 0–1 score with its bar; both turn destructive below the pass score. */
export function ScoreBar({ value, pass }: { value: number | null; pass: number }) {
  if (value === null) return <span className="text-ink-3">—</span>;
  const low = value < pass;
  return (
    <div className="flex min-w-0 items-center gap-2 text-[12px]">
      <span className={cn("w-[30px] text-end tabular-nums", low && "text-destructive")}>
        {score(value)}
      </span>
      <span className="h-1 min-w-7 flex-1 overflow-hidden rounded-full bg-selection">
        <span
          className={cn("block h-full rounded-full", low ? "bg-destructive" : "bg-ink-2")}
          style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }}
        />
      </span>
    </div>
  );
}

const TRY_DOT: Record<TryOutcome, string> = {
  passed: "bg-foreground/20",
  failed: "bg-destructive",
  unjudged: "ring-1 ring-inset ring-ink-3",
};

/** One dot per try: quiet when it passed, destructive when it failed, hollow when unjudged. */
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

/** Where the model reads the fault to be, as a small outlined tag. */
export function SuspectTag({ suspect }: { suspect: Suspect }) {
  return (
    <span className="inline-flex h-[18px] shrink-0 items-center rounded-[5px] px-1.5 text-[11px] font-medium text-ink-2 ring-1 ring-inset ring-separator">
      {SUSPECT_LABEL[suspect]}
    </span>
  );
}

/** A test that may be out of date with its app, and why on hover. */
export function StaleChip({ reason }: { reason: string }) {
  return (
    <span
      title={reason}
      className="shrink-0 rounded-[6px] bg-warning/10 px-1.5 py-px text-[11.5px] text-warning"
    >
      May be stale
    </span>
  );
}

export const VERDICT_CELL: Record<Verdict, string> = {
  pass: "bg-success",
  flaky: "bg-warning",
  fail: "bg-destructive",
  unjudged: "ring-1 ring-inset ring-ink-3",
};

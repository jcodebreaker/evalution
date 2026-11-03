import { cn } from "../../lib/cn";
import { seconds, spent } from "../../lib/format";
import type { Compare, TryOutcome } from "../../lib/schema";
import { NEVER_REACHED, PASSED_MODEL_ONLY, retriedWords, tryWord } from "../../lib/verdict";

const MODEL_ONLY = PASSED_MODEL_ONLY.split(", ")[1];
const TONE: Record<TryOutcome, [string, string, string]> = {
  passed: ["bg-success/12", "text-success", "✓"],
  failed: ["bg-destructive/10", "text-destructive", "✕"],
  unjudged: ["bg-bubble", "text-ink-2", "?"],
};
const DOT: Record<TryOutcome, string> = {
  passed: "bg-success",
  failed: "bg-destructive",
  unjudged: "ring-1 ring-inset ring-ink-3",
};

/** The try shown, failed or passed, with the way to the other tries. */
export function VerdictBanner({
  shown,
  runLabel,
  onTry,
}: {
  shown: Compare;
  runLabel: (run: string) => string;
  onTry: (n: number) => void;
}) {
  const others = shown.tries.filter((entry) => entry.n !== shown.try);
  const those = (outcome: TryOutcome) =>
    others.filter((entry) => entry.outcome === outcome).map((entry) => entry.n);
  const tries = (ns: number[]) =>
    `${ns.length === 1 ? "Try" : "Tries"} ${ns.slice(0, -1).join(", ")}${ns.length > 1 ? " and " : ""}${ns[ns.length - 1]}`;
  const said = (ns: number[], words: string) => (ns.length ? `${tries(ns)} ${words}.` : null);
  const rest = [
    said(those("passed"), "passed"),
    said(those("failed"), "failed"),
    said(those("unjudged"), "couldn't be judged"),
  ];
  const lead = shown.tries.length === 1 ? "" : ` on try ${shown.try}`;
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-3 rounded-[14px] px-4 py-3.5",
        TONE[shown.outcome][0],
      )}
    >
      <span className={cn("text-[20px]", TONE[shown.outcome][1])}>{TONE[shown.outcome][2]}</span>
      <div className="flex min-w-0 flex-1 flex-col">
        <b className="text-[15px]">
          {`${shown.label ?? tryWord(shown.outcome)}${lead}${shown.passed && shown.different_route ? ", by a different route" : ""}${shown.passed && shown.basis === "model_only" ? `, ${MODEL_ONLY}` : ""}`}
        </b>
        <small className="text-[12.5px] text-ink-2">
          {[
            shown.passed ? null : shown.reason,
            shown.called ? null : NEVER_REACHED,
            ...retriedWords(shown.retried),
            ...rest,
            runLabel(shown.run),
          ]
            .filter(Boolean)
            .join(" ")}
          {[shown.seconds === null ? null : seconds(shown.seconds), spent(shown.usd ?? 0)]
            .filter(Boolean)
            .map((part) => ` · ${part}`)
            .join("")}
        </small>
      </div>
      {shown.tries.length > 1 ? (
        <fieldset className="m-0 flex flex-wrap gap-1.5 border-0 p-0" aria-label="Tries">
          {shown.tries.map((entry) => (
            <button
              key={entry.n}
              type="button"
              aria-pressed={entry.n === shown.try}
              onClick={() => onTry(entry.n)}
              className={cn(
                "flex h-7 items-center gap-1.5 rounded-[7px] bg-group px-2.5 text-[12.5px] ring-[0.5px] ring-separator focus-visible:outline-2 focus-visible:outline-ring",
                entry.n === shown.try && "ring-2 ring-tint",
              )}
            >
              <i className={cn("size-2 rounded-full", DOT[entry.outcome])} />
              Try {entry.n}
            </button>
          ))}
        </fieldset>
      ) : null}
    </div>
  );
}

import { cn } from "../../lib/cn";
import type { PlanItem } from "../../lib/schema";
import { checkWord } from "../../lib/verdict";
import { Outcome } from "./Outcome";

const JUDGED: Record<PlanItem["judged"], [string, string]> = {
  exact: ["checked exactly", "code"],
  steps: ["judged from the steps", "model"],
  graded: ["graded", "model"],
};

export function Check({
  item,
  names,
  result,
  onSpan,
}: {
  item: PlanItem;
  names: Record<string, string>;
  result: boolean;
  onSpan?: (span: string) => void;
}) {
  const actual = result ? item.actual : undefined;
  const mark = actual
    ? actual.unjudged
      ? ["?", "bg-bubble text-ink-2"]
      : actual.passed
        ? ["✓", "bg-success/12 text-success"]
        : ["✕", "bg-destructive/10 text-destructive"]
    : item.mode === "must"
      ? ["✓", "bg-selection-strong text-tint"]
      : item.mode === "never"
        ? ["⊘", "bg-bubble text-ink-2"]
        : ["·", "bg-bubble text-ink-2"];
  const label = item.mode === "must" ? "Must" : item.mode === "never" ? "Never" : "Allowed";
  return (
    <div className="grid grid-cols-[22px_minmax(0,1fr)_auto] items-start gap-2.5 border-separator px-3.5 py-2.5 not-first:border-t">
      <span
        role="img"
        aria-label={actual ? checkWord(actual) : label}
        className={cn(
          "grid size-[22px] place-items-center rounded-[7px] text-[12px] font-bold",
          mark[1],
        )}
      >
        {mark[0]}
      </span>
      <div className="min-w-0">
        <p className="m-0 text-[13.5px]">{item.text}</p>
        {item.detail && !actual ? (
          <small className="mt-px block text-[12px] text-ink-3">{item.detail}</small>
        ) : null}
        {actual ? <Outcome actual={actual} names={names} onSpan={onSpan} /> : null}
      </div>
      <span className="text-[11.5px] whitespace-nowrap text-ink-3">
        {item.mode === "allowed" && !actual ? "" : JUDGED[item.judged][result ? 1 : 0]}
      </span>
    </div>
  );
}

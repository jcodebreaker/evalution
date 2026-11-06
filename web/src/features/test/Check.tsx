import { cn } from "../../lib/cn";
import type { LineAgreement, PlanItem } from "../../lib/schema";
import { checkWord } from "../../lib/verdict";
import { AGREE_KEYS, Agree } from "./Agree";
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
  agreement,
  onLabel,
}: {
  item: PlanItem;
  names: Record<string, string>;
  result: boolean;
  onSpan?: (span: string) => void;
  /** How often the grader agrees with a person on this line, for a graded line of a try. */
  agreement?: LineAgreement;
  /** Agree or not with the grader here; `null` clears the answer. */
  onLabel?: (agree: boolean | null) => void;
}) {
  const actual = result ? item.actual : undefined;
  const answerable = actual && agreement && onLabel ? agreement : undefined;
  const press = (agree: boolean) => onLabel?.(answerable?.mine === agree ? null : agree);
  const Row = answerable ? "fieldset" : "div";
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
    // A graded line of a try takes focus: one key agrees or disagrees with the grader.
    <Row
      aria-label={answerable ? item.text : undefined}
      tabIndex={answerable?.can ? 0 : undefined}
      aria-keyshortcuts={answerable?.can ? Object.keys(AGREE_KEYS).join(" ") : undefined}
      className="grid min-w-0 grid-cols-[22px_minmax(0,1fr)_auto] items-start gap-2.5 border-separator px-3.5 py-2.5 outline-none not-first:border-t focus-visible:bg-selection"
      onKeyDown={
        answerable?.can
          ? (event) => {
              const agree = AGREE_KEYS[event.key];
              if (agree !== undefined && !event.metaKey && !event.ctrlKey && !event.altKey) {
                event.preventDefault();
                press(agree);
              }
            }
          : undefined
      }
    >
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
        {answerable ? <Agree agreement={answerable} onPress={press} /> : null}
      </div>
      <span className="text-[11.5px] whitespace-nowrap text-ink-3">
        {item.mode === "allowed" && !actual ? "" : JUDGED[item.judged][result ? 1 : 0]}
      </span>
    </Row>
  );
}

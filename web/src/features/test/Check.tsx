import type { LineAgreement, PlanItem } from "../../lib/schema";
import { checkWord } from "../../lib/verdict";
import { StatusDot } from "../../ui/status";
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
  const label = item.mode === "must" ? "Must" : item.mode === "never" ? "Never" : "Allowed";
  const how = item.mode === "allowed" && !actual ? "" : JUDGED[item.judged][result ? 1 : 0];
  return (
    // A graded line of a try takes focus: one key agrees or disagrees with the grader.
    <Row
      aria-label={answerable ? item.text : undefined}
      tabIndex={answerable?.can ? 0 : undefined}
      aria-keyshortcuts={answerable?.can ? Object.keys(AGREE_KEYS).join(" ") : undefined}
      className="grid min-w-0 grid-cols-[12px_minmax(0,1fr)_auto] items-start gap-2.5 border-separator px-3.5 py-2.5 outline-none not-first:border-t focus-visible:bg-selection"
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
      <span className="flex justify-center pt-[7px]">
        {actual ? (
          <StatusDot
            state={actual.unjudged ? "unjudged" : actual.passed ? "pass" : "fail"}
            label={checkWord(actual)}
          />
        ) : (
          <span aria-hidden className="size-2 rounded-full bg-rule-strong" />
        )}
      </span>
      <div className="min-w-0">
        <p className="m-0 text-sm">{item.text}</p>
        {item.detail && !actual ? (
          <small className="mt-px block text-sm text-ink-3">{item.detail}</small>
        ) : null}
        {actual ? <Outcome actual={actual} names={names} onSpan={onSpan} /> : null}
        {answerable ? <Agree agreement={answerable} onPress={press} /> : null}
      </div>
      <span className="pt-0.5 text-[13px] whitespace-nowrap text-ink-3">
        {[actual ? null : label, how].filter(Boolean).join(" · ")}
      </span>
    </Row>
  );
}

import { count } from "../../lib/format";
import type { LineAgreement, PlanStep } from "../../lib/schema";
import { Check } from "./Check";
import { Outcome } from "./Outcome";

export function StepBody({
  step,
  names,
  result,
  onSpan,
  agreement,
  onLabel,
}: {
  step: PlanStep;
  names: Record<string, string>;
  result: boolean;
  onSpan?: (span: string) => void;
  /** By rubric item id. */
  agreement?: Record<string, LineAgreement>;
  onLabel?: (item: string, agree: boolean | null) => void;
}) {
  const files = step.kind === "before" || !result ? step.files : [];
  // Once run, a titled turn speaks through its title and outcome.
  const message = step.kind === "say" && step.message && !(result && step.title);
  return (
    <div className="flex flex-col gap-2">
      {message || files.length ? (
        <div className="group-card flex flex-col">
          {message ? (
            <div className="px-3.5 py-3">
              <p className="m-0 w-fit max-w-[560px] rounded-xl bg-bubble px-3 py-2 text-[14px] whitespace-pre-wrap">
                {step.message}
              </p>
            </div>
          ) : null}
          {files.map((entry) => (
            <div
              key={entry.name}
              className="flex min-w-0 flex-col border-separator not-first:border-t"
            >
              <span className="m-3 inline-flex w-fit max-w-[calc(100%-24px)] flex-wrap items-center gap-x-2 gap-y-0.5 rounded-[9px] px-2.5 py-1.5 text-[13px] ring-1 ring-inset ring-separator">
                <code className="font-mono text-[12.5px]">{entry.name}</code>
                {[
                  entry.rows !== undefined ? count(entry.rows, "row") : null,
                  entry.columns?.length ? entry.columns.join(", ") : null,
                ]
                  .filter(Boolean)
                  .map((part) => (
                    <span key={part} className="text-ink-2">
                      · {part}
                    </span>
                  ))}
              </span>
              {step.kind === "before" && entry.peek ? (
                <pre className="m-0 overflow-x-auto border-t border-separator px-3.5 py-2 font-mono text-[11.5px] text-ink-2">
                  {entry.peek}
                  {entry.more ? `   … ${entry.more} more` : ""}
                </pre>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
      {step.items.length ? (
        <div className="group-card">
          {step.items.map((item) => (
            <Check
              key={item.id}
              item={item}
              names={names}
              result={result}
              onSpan={onSpan}
              agreement={agreement?.[item.id]}
              onLabel={onLabel ? (agree) => onLabel(item.id, agree) : undefined}
            />
          ))}
        </div>
      ) : null}
      {step.reference && !result ? (
        <p className="m-0 text-[13px] text-ink-2">
          A good reply, for reference: “{step.reference}”
        </p>
      ) : null}
      {step.note && !(result && step.actual) ? (
        <p className="m-0 text-[13px] text-ink-2">{step.note}</p>
      ) : null}
      {result && step.actual ? <Outcome actual={step.actual} /> : null}
    </div>
  );
}

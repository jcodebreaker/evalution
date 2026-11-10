import { seconds } from "../../lib/format";
import { type BeforeWords, beforeWords } from "../../lib/isolation";
import type { Actual, LineAgreement, PlanStep, TryIsolation } from "../../lib/schema";
import { type Outcome, StatusDot } from "../../ui/status";
import { PageShown } from "./Page";
import { StepBody } from "./StepBody";

/** What a try started from, under its "Before" step. */
function BeforeNote({ words }: { words: BeforeWords }) {
  return (
    <div className="mt-1 flex flex-col gap-0.5 text-[13px]" data-testid="before-isolation">
      {words.line ? <p className="m-0 text-ink-2">{words.line}</p> : null}
      {words.warning ? <p className="m-0 font-semibold text-warning">{words.warning}</p> : null}
    </div>
  );
}

function kindLabel(kind: PlanStep["kind"], result: boolean, app: string): string {
  switch (kind) {
    case "before":
      return "Before";
    case "say":
      return "The person says";
    case "filler":
      return "Ordinary chat";
    case "break":
      return "Break";
    case "state":
      return result ? "What the app holds" : "What the app should hold";
    case "agent":
      return result ? `What ${app} did` : `What ${app} should do`;
    case "reply":
      return result ? "What the reply said" : "What the reply should say";
    case "passes":
      return "Passes when";
  }
}

/** How a step came out in the try shown: failed when anything in it failed, passed when all of it
 * held, else couldn't judge; `none` when nothing in it was checked. */
function markOf(step: PlanStep): Outcome {
  // What the page showed of a message counts as much as its being sent.
  const page = step.page?.check
    ? {
        passed: step.page.check.outcome === "passed",
        unjudged: step.page.check.outcome === "unjudged",
      }
    : undefined;
  const outcomes = [step.actual, page, ...step.items.map((item) => item.actual)].filter(
    (entry): entry is Pick<Actual, "passed" | "unjudged"> => !!entry,
  );
  if (!outcomes.length) return "none";
  const decided = outcomes.filter((entry) => !entry.unjudged);
  if (decided.some((entry) => !entry.passed)) return "fail";
  return decided.length === outcomes.length ? "pass" : "unjudged";
}

const SAID: Record<Outcome, string> = {
  pass: "Held",
  fail: "Failed",
  unjudged: "Couldn't judge",
  none: "Nothing checked",
};

/** The test's steps on one vertical line; under each check, what happened when `result`. */
export function Steps({
  steps,
  names,
  appName,
  result,
  onSpan,
  agreement,
  onLabel,
  isolation,
}: {
  steps: PlanStep[];
  names: Record<string, string>;
  appName: string;
  result: boolean;
  onSpan?: (span: string) => void;
  /** By rubric item id: how often the grader agrees with a person on each line. */
  agreement?: Record<string, LineAgreement>;
  onLabel?: (item: string, agree: boolean | null) => void;
  /** How the try shown was kept from earlier ones, said in its "Before" step. */
  isolation?: TryIsolation | null;
}) {
  let number = 0;
  return (
    <ol className="m-0 flex list-none flex-col p-0">
      {steps.map((step, index) => {
        const numbered = step.kind !== "before" && step.kind !== "passes";
        if (numbered) number += 1;
        // A try's "Before" says what it started from: put back and checked, a new person, or not.
        const before =
          result && step.kind === "before" && isolation ? beforeWords(isolation, step.title) : null;
        const mark: Outcome | null = before
          ? before.clean
            ? "pass"
            : before.warning
              ? "unjudged"
              : "none"
          : result
            ? markOf(step)
            : null;
        const title = before ? before.title : step.title;
        // When the page showed the message's reply, from the try's first message.
        const at = result ? (step.page?.shown_at ?? null) : null;
        return (
          <li
            key={index}
            className="relative grid grid-cols-[20px_minmax(0,1fr)_auto] gap-x-3 py-2.5 before:absolute before:top-[30px] before:-bottom-[10px] before:left-[9.5px] before:w-px before:bg-separator last:before:hidden"
          >
            <span className="flex justify-center pt-[7px]">
              {mark ? (
                <StatusDot state={mark} label={SAID[mark]} />
              ) : (
                <span aria-hidden className="size-2 rounded-full bg-rule-strong" />
              )}
            </span>
            <div className="flex min-w-0 flex-col">
              <span className="text-sm text-ink-3">
                {numbered ? `Step ${number} · ` : ""}
                {kindLabel(step.kind, result, appName)}
              </span>
              {title ? <h3 className="m-0 text-sm font-medium">{title}</h3> : null}
              <div className="mt-1.5 flex flex-col">
                <StepBody
                  step={before ? { ...step, actual: undefined } : step}
                  names={names}
                  result={result}
                  onSpan={onSpan}
                  agreement={agreement}
                  onLabel={onLabel}
                />
                {before ? <BeforeNote words={before} /> : null}
                {result && step.page ? <PageShown page={step.page} /> : null}
              </div>
            </div>
            <span className="pt-0.5 font-mono text-xs text-ink-3 tabular-nums">
              {at === null ? "" : seconds(at, true)}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

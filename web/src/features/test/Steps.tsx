import {
  CheckCheck,
  Cog,
  Database,
  MessageSquare,
  MessagesSquare,
  PenLine,
  RotateCcw,
  Split,
} from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import { type BeforeWords, beforeWords } from "../../lib/isolation";
import type { Actual, LineAgreement, PlanStep, TryIsolation } from "../../lib/schema";
import { PageShown } from "./Page";
import { StepBody } from "./StepBody";

/** What a try started from, under its "Before" step. */
function BeforeNote({ words }: { words: BeforeWords }) {
  return (
    <div className="mt-1 flex flex-col gap-0.5 text-[13px]" data-testid="before-isolation">
      {words.line ? (
        <p className={cn("m-0", words.clean ? "font-semibold text-success" : "text-ink-2")}>
          {words.line}
        </p>
      ) : null}
      {words.warning ? <p className="m-0 font-semibold text-warning">{words.warning}</p> : null}
    </div>
  );
}

const ICON: Record<PlanStep["kind"], ReactNode> = {
  before: <RotateCcw />,
  say: <MessageSquare />,
  filler: <MessagesSquare />,
  break: <Split />,
  state: <Database />,
  agent: <Cog />,
  reply: <PenLine />,
  passes: <CheckCheck />,
};

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

/** Whether a step held in the try shown: `null` before a run or when nothing says. */
function held(step: PlanStep): boolean | null {
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
  const decided = outcomes.filter((entry) => !entry.unjudged);
  if (decided.some((entry) => !entry.passed)) return false;
  return decided.length === outcomes.length && decided.length ? true : null;
}

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
  let message = 0;
  return (
    <ol className="m-0 flex list-none flex-col p-0">
      {steps.map((step, index) => {
        const numbered = step.kind !== "before" && step.kind !== "passes";
        if (numbered) number += 1;
        if (step.kind === "say") message += 1;
        // A try's "Before" says what it started from: put back and checked, a new person, or not.
        const before =
          result && step.kind === "before" && isolation ? beforeWords(isolation, step.title) : null;
        const ok = before ? before.clean : result ? held(step) : null;
        const title = before ? before.title : step.title;
        return (
          <li
            key={index}
            className="relative grid grid-cols-[34px_minmax(0,1fr)] gap-3.5 before:absolute before:top-[34px] before:-bottom-1 before:left-4 before:w-0.5 before:bg-separator last:before:hidden"
          >
            <span
              className={cn(
                "z-[1] grid size-[34px] place-items-center rounded-full bg-group ring-1 ring-separator [&_svg]:size-4",
                ok === true && "bg-success/12 text-success",
                ok === false && "bg-destructive/10 text-destructive",
                ok === null && "text-ink-2",
              )}
              aria-hidden
            >
              {ICON[step.kind]}
            </span>
            <div className="flex min-w-0 flex-col pt-1 pb-6">
              <span className="text-[11.5px] font-semibold tracking-[0.02em] text-ink-3 uppercase">
                {numbered ? `Step ${number} · ` : ""}
                {kindLabel(step.kind, result, appName)}
              </span>
              {title ? (
                <h3 className="m-0 mt-px mb-2 text-[15px] font-semibold">{title}</h3>
              ) : (
                <div className="h-2" />
              )}
              <StepBody
                step={before ? { ...step, actual: undefined } : step}
                names={names}
                result={result}
                onSpan={onSpan}
                agreement={agreement}
                onLabel={onLabel}
              />
              {before ? <BeforeNote words={before} /> : null}
              {result && step.page ? <PageShown page={step.page} n={message} /> : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

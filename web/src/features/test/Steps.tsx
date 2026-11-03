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
import type { Actual, PlanStep } from "../../lib/schema";
import { StepBody } from "./StepBody";

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
  const outcomes = [step.actual, ...step.items.map((item) => item.actual)].filter(
    (entry): entry is Actual => !!entry,
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
}: {
  steps: PlanStep[];
  names: Record<string, string>;
  appName: string;
  result: boolean;
  onSpan?: (span: string) => void;
}) {
  let number = 0;
  return (
    <ol className="m-0 flex list-none flex-col p-0">
      {steps.map((step, index) => {
        const numbered = step.kind !== "before" && step.kind !== "passes";
        if (numbered) number += 1;
        const ok = result ? held(step) : null;
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
              {step.title ? (
                <h3 className="m-0 mt-px mb-2 text-[15px] font-semibold">{step.title}</h3>
              ) : (
                <div className="h-2" />
              )}
              <StepBody step={step} names={names} result={result} onSpan={onSpan} />
            </div>
          </li>
        );
      })}
    </ol>
  );
}

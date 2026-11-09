import { Check, CircleAlert } from "lucide-react";
import { cn } from "../../lib/cn";
import type { IsolationStep } from "../../lib/schema";
import { stepTime } from "./words";

/** The agent's steps as they go: waiting, running, done with its time, or failed with why. */
export function Checklist({ steps }: { steps: readonly IsolationStep[] }) {
  return (
    <ol className="m-0 flex list-none flex-col gap-2 p-0" aria-label="Steps">
      {steps.map((step) => (
        <li key={step.key} className="flex flex-col gap-1" data-state={step.state}>
          <div
            className={cn(
              "flex items-center gap-2.5",
              step.state === "waiting" && "text-ink-3",
              step.state === "running" && "font-medium text-ink",
              step.state === "done" && "text-ink-2",
              step.state === "failed" && "font-medium text-warning",
            )}
          >
            <Mark state={step.state} />
            <span className="min-w-0">{step.label}</span>
            {step.seconds !== null && step.state !== "waiting" ? (
              <span className="ms-auto text-[12.5px] text-ink-3 tabular-nums">
                {stepTime(step.seconds)}
              </span>
            ) : null}
          </div>
          {step.state === "failed" && step.detail ? (
            <p className="m-0 ps-7 text-[13px] text-warning">{step.detail}</p>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

function Mark({ state }: { state: IsolationStep["state"] }) {
  if (state === "done") {
    return (
      <span className="grid size-[18px] shrink-0 place-items-center rounded-full bg-up text-success">
        <Check className="size-3" aria-label="Done" />
      </span>
    );
  }
  if (state === "failed") {
    return <CircleAlert className="size-[18px] shrink-0 text-warning" aria-label="Failed" />;
  }
  if (state === "running") {
    return (
      <span
        role="status"
        aria-label="Running"
        className="size-[18px] shrink-0 animate-spin rounded-full border-[1.5px] border-tint border-t-transparent"
      />
    );
  }
  return (
    <span
      role="img"
      aria-label="Waiting"
      className="size-[18px] shrink-0 rounded-full border-[1.5px] border-input"
    />
  );
}

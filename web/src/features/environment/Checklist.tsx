import { cn } from "../../lib/cn";
import type { IsolationStep } from "../../lib/schema";
import { StatusDot } from "../../ui/status";
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
              step.state === "failed" && "font-medium text-fail",
            )}
          >
            <Mark state={step.state} />
            <span className="min-w-0">{step.label}</span>
            {step.seconds !== null && step.state !== "waiting" ? (
              <span className="ms-auto text-sm text-ink-3 tabular-nums">
                {stepTime(step.seconds)}
              </span>
            ) : null}
          </div>
          {step.state === "failed" && step.detail ? (
            <p className="m-0 ps-[22px] text-sm text-fail">{step.detail}</p>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

/** A step's mark, in a 12px slot: the outcome dot when done or failed, a small spinner while it
 * runs, the hollow dot while it waits. */
export function Mark({ state }: { state: IsolationStep["state"] }) {
  return (
    <span className="grid w-3 shrink-0 place-items-center">
      {state === "running" ? (
        <span
          role="status"
          aria-label="Running"
          className="size-3 animate-spin rounded-full border-[1.5px] border-tint border-t-transparent"
        />
      ) : (
        <StatusDot
          state={state === "done" ? "pass" : state === "failed" ? "fail" : "none"}
          label={state === "done" ? "Done" : state === "failed" ? "Failed" : "Waiting"}
        />
      )}
    </span>
  );
}

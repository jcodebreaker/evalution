import { Fragment } from "react";
import { cn } from "../../lib/cn";
import { StatusDot } from "../../ui/status";

export type ProgressState = "done" | "now" | "waiting";

/** Getting started, at a glance: Connect → Learn → Set up testing → First test. */
export function Progress({ steps }: { steps: readonly { label: string; state: ProgressState }[] }) {
  return (
    <ol
      className="m-0 flex list-none flex-wrap items-center gap-2.5 p-0 text-[14px]"
      aria-label="Getting started"
    >
      {steps.map((step, index) => (
        <Fragment key={step.label}>
          {index ? (
            <li aria-hidden className="h-px max-w-[60px] min-w-4 flex-1 bg-rule-strong" />
          ) : null}
          <li
            className={cn(
              "flex items-center gap-2",
              step.state === "done" && "text-ink-2",
              step.state === "now" && "font-medium text-ink",
              step.state === "waiting" && "text-ink-3",
            )}
            aria-current={step.state === "now" ? "step" : undefined}
          >
            {step.state === "now" ? (
              <span
                aria-hidden
                className="size-2 shrink-0 rounded-full bg-tint ring-3 ring-tint-soft-2"
              />
            ) : (
              <StatusDot
                state={step.state === "done" ? "pass" : "none"}
                label={step.state === "done" ? "Done" : "Not yet"}
              />
            )}
            {step.label}
          </li>
        </Fragment>
      ))}
    </ol>
  );
}

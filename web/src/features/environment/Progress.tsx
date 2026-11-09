import { Check } from "lucide-react";
import { Fragment } from "react";
import { cn } from "../../lib/cn";

export type ProgressState = "done" | "now" | "waiting";

/** Getting started, at a glance: Connect → Learn → Set up testing → First test. */
export function Progress({ steps }: { steps: readonly { label: string; state: ProgressState }[] }) {
  return (
    <ol
      className="m-0 flex list-none flex-wrap items-center gap-2.5 p-0 text-[14px] font-medium"
      aria-label="Getting started"
    >
      {steps.map((step, index) => (
        <Fragment key={step.label}>
          {index ? (
            <li aria-hidden className="h-[1.5px] max-w-[60px] min-w-4 flex-1 bg-border" />
          ) : null}
          <li
            className={cn(
              "flex items-center gap-2",
              step.state === "done" && "text-ink-2",
              step.state === "now" && "text-ink",
              step.state === "waiting" && "text-ink-3",
            )}
            aria-current={step.state === "now" ? "step" : undefined}
          >
            <span
              className={cn(
                "grid size-[22px] place-items-center rounded-full text-[12.5px]",
                step.state === "done" && "bg-up text-success",
                step.state === "now" && "bg-tint text-white",
                step.state === "waiting" && "bg-muted text-ink-3",
              )}
            >
              {step.state === "done" ? <Check className="size-3.5" aria-label="Done" /> : index + 1}
            </span>
            {step.label}
          </li>
        </Fragment>
      ))}
    </ol>
  );
}

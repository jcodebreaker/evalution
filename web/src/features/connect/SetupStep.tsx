import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import { StatusDot } from "../../ui/status";

export type StepState = "waiting" | "now" | "done" | "problem";

/** One step of connecting an app: its number or how it ended, its title, then what it needs. */
export function SetupStep({
  n,
  title,
  state,
  children,
}: {
  n: number;
  title: string;
  state: StepState;
  children?: ReactNode;
}) {
  return (
    <section className="flex min-w-0 flex-col gap-3" aria-label={title}>
      <h2
        className={cn(
          "m-0 flex items-center gap-2 text-[15px] font-semibold",
          state === "waiting" && "font-medium text-ink-3",
        )}
      >
        <span className="grid w-4 shrink-0 place-items-center font-mono text-[13px] font-normal text-ink-3 tabular-nums">
          {state === "done" ? (
            <StatusDot state="pass" label="Done" />
          ) : state === "problem" ? (
            <StatusDot state="unjudged" label="Needs attention" />
          ) : (
            n
          )}
        </span>
        {title}
      </h2>
      {state !== "waiting" && children ? (
        <div className="flex min-w-0 flex-col gap-3">{children}</div>
      ) : null}
    </section>
  );
}

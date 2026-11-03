import { Check, CircleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

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
          "m-0 flex items-center gap-2 text-[14px] font-semibold",
          state === "waiting" && "font-medium text-ink-3",
        )}
      >
        <span className="grid size-5 shrink-0 place-items-center rounded-md bg-selection text-[11px] font-semibold text-ink-2">
          {state === "done" ? (
            <Check className="size-3.5 text-success" aria-label="Done" />
          ) : state === "problem" ? (
            <CircleAlert className="size-3.5 text-warning" aria-label="Needs attention" />
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

import { cn } from "../lib/cn";

/** An outcome, as one mark everywhere: a small solid dot whose colour carries it, with the words
 * beside it saying it (docs/mockups/design-studio.html). Never a tick or a cross in a circle. */
export type Outcome = "pass" | "fail" | "unjudged" | "none";

const FILL: Record<Outcome, string> = {
  pass: "bg-pass",
  fail: "bg-fail",
  unjudged: "bg-unjudged",
  none: "bg-transparent ring-[1.5px] ring-inset ring-rule-strong",
};

const SAID: Record<Outcome, string> = {
  pass: "Passed",
  fail: "Failed",
  unjudged: "Couldn't judge",
  none: "Not run",
};

/** The dot alone. `label` names it for screen readers and on hover; defaults to the outcome. */
export function StatusDot({
  state,
  label,
  className,
}: {
  state: Outcome;
  label?: string;
  className?: string;
}) {
  return (
    <span
      role="img"
      aria-label={label ?? SAID[state]}
      title={label ?? SAID[state]}
      className={cn(
        "inline-block size-2 shrink-0 rounded-full align-middle",
        FILL[state],
        className,
      )}
    />
  );
}

/** The dot with its words: "● Failed". */
export function Status({
  state,
  children,
  className,
}: {
  state: Outcome;
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-[13px] text-ink-2", className)}>
      <StatusDot state={state} label={typeof children === "string" ? children : undefined} />
      {children ?? SAID[state]}
    </span>
  );
}

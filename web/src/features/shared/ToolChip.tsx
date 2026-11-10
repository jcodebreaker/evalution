import type { ComponentProps } from "react";
import { cn } from "../../lib/cn";

export type ChipTone = "plain" | "expected" | "never" | "wrong" | "missing" | "extra";

const CHIP_TONE: Record<ChipTone, string> = {
  plain: "bg-sunk",
  expected: "bg-group ring-1 ring-inset ring-separator",
  never: "text-ink-3 line-through ring-1 ring-inset ring-separator",
  wrong: "bg-fail-soft text-fail ring-1 ring-inset ring-fail/40",
  missing: "border border-dashed border-fail text-fail",
  extra: "bg-sunk",
};

/** A tool call as a chip, marked against what was expected. */
export function ToolChip({
  label,
  tone,
  selected,
  banned,
  className,
  ...props
}: {
  label: string;
  tone: ChipTone;
  selected?: boolean;
  /** A call to a tool the test says never to call. */
  banned?: boolean;
} & Omit<ComponentProps<"button">, "children">) {
  // No icons: a call the test didn't expect carries a "+", a banned one is red and says so.
  const content = (
    <>
      {tone === "extra" && !banned ? (
        <span aria-hidden className="text-ink-3">
          +
        </span>
      ) : null}
      <span className="truncate">{label}</span>
    </>
  );
  const classes = cn(
    "inline-flex h-6 max-w-full items-center gap-1 whitespace-nowrap rounded-full px-2.5 font-mono text-xs",
    CHIP_TONE[tone],
    banned && "bg-fail-soft text-fail ring-1 ring-inset ring-fail/40",
    selected && "ring-2 ring-inset ring-tint",
    className,
  );
  const title = banned ? `${label}: the test says never to call it` : undefined;
  if (!props.onClick)
    return (
      <span className={classes} title={title}>
        {content}
      </span>
    );
  return (
    <button
      type="button"
      className={cn(
        classes,
        "hover:brightness-95 focus-visible:outline-2 focus-visible:outline-tint",
      )}
      title={title}
      {...props}
    >
      {content}
    </button>
  );
}

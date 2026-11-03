import { Ban, Plus, Zap } from "lucide-react";
import type { ComponentProps } from "react";
import { cn } from "../../lib/cn";

export type ChipTone = "plain" | "expected" | "never" | "wrong" | "missing" | "extra";

const CHIP_TONE: Record<ChipTone, string> = {
  plain: "bg-bubble",
  expected: "ring-1 ring-inset ring-separator",
  never: "text-ink-3 line-through ring-1 ring-inset ring-separator",
  wrong: "bg-bubble ring-[1.5px] ring-inset ring-destructive",
  missing: "border-[1.5px] border-dashed border-destructive text-destructive",
  extra: "bg-bubble",
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
  const Icon = banned ? Ban : tone === "never" ? null : tone === "extra" ? Plus : Zap;
  const markIcon = tone === "wrong" || tone === "missing" ? "text-destructive" : "text-ink-3";
  const content = (
    <>
      {Icon ? <Icon className={cn("size-[11px] shrink-0", markIcon)} aria-hidden /> : null}
      <span className="truncate">{label}</span>
    </>
  );
  const classes = cn(
    "inline-flex h-6 max-w-full items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 font-mono text-[11.5px]",
    CHIP_TONE[tone],
    selected && "ring-2 ring-inset ring-primary",
    className,
  );
  if (!props.onClick) return <span className={classes}>{content}</span>;
  return (
    <button
      type="button"
      className={cn(
        classes,
        "hover:brightness-95 focus-visible:outline-2 focus-visible:outline-ring",
      )}
      {...props}
    >
      {content}
    </button>
  );
}

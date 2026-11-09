import type { ComponentProps } from "react";
import { cn } from "../lib/cn";

// The buttons of docs/mockups/design-studio.html, on a native button: the one primary action solid
// purple; the rest on a 1px line; never a shadow.

const VARIANT = {
  /** The screen's one primary action: solid purple. */
  default:
    "border-tint bg-tint text-primary-foreground hover:border-tint-ink hover:bg-tint-ink disabled:opacity-40",
  /** Every other action: white on a 1px line. */
  secondary: "border-rule-strong bg-group text-ink hover:border-ink-3 hover:bg-sunk",
  /** The same as `secondary`, kept for its callers. */
  outline: "border-rule-strong bg-group text-ink hover:border-ink-3 hover:bg-sunk",
  /** A quiet action, or an icon alone: no line until hovered. */
  ghost: "text-ink-2 hover:bg-sunk hover:text-ink",
  /** A way to another screen, as words in purple: "See why →". */
  link: "h-auto px-1 text-tint-ink hover:underline hover:underline-offset-[3px]",
  destructive: "border-fail-soft bg-fail-soft text-fail hover:border-fail/40",
} as const;

const SIZE = {
  default: "h-8 gap-2 px-3 [&_svg:not([class*='size-'])]:size-4",
  lg: "h-10 gap-2 px-4 text-[14px] [&_svg:not([class*='size-'])]:size-4",
  sm: "h-7 gap-1.5 px-2.5 text-[13px] [&_svg:not([class*='size-'])]:size-3.5",
  xs: "h-6 gap-1 rounded-[5px] px-2 text-xs [&_svg:not([class*='size-'])]:size-3",
  icon: "size-8",
  "icon-sm": "size-7",
  "icon-xs": "size-6 rounded-[5px] [&_svg:not([class*='size-'])]:size-3.5",
} as const;

export type ButtonProps = ComponentProps<"button"> & {
  variant?: keyof typeof VARIANT;
  size?: keyof typeof SIZE;
};

export function buttonClass(
  variant: keyof typeof VARIANT = "default",
  size: keyof typeof SIZE = "default",
) {
  return cn(
    "inline-flex shrink-0 items-center justify-center rounded-[6px] border border-transparent text-[14px] font-medium whitespace-nowrap transition-colors outline-none select-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-tint active:translate-y-px disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
    SIZE[size],
    VARIANT[variant],
  );
}

export function Button({ className, variant, size, type = "button", ...props }: ButtonProps) {
  return <button type={type} className={cn(buttonClass(variant, size), className)} {...props} />;
}

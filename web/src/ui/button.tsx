import type { ComponentProps } from "react";
import { cn } from "../lib/cn";

// shadcn's Button, written locally: the same variants and sizes, on a native button.

const VARIANT = {
  default: "bg-primary text-primary-foreground hover:bg-primary/85",
  secondary: "bg-secondary text-secondary-foreground hover:bg-foreground/10",
  ghost: "hover:bg-selection hover:text-foreground",
  outline: "border-border bg-background hover:bg-muted",
  destructive: "bg-destructive/10 text-destructive hover:bg-destructive/20",
} as const;

const SIZE = {
  default: "h-8 gap-1.5 px-3 [&_svg:not([class*='size-'])]:size-3.5",
  lg: "h-10 gap-2 rounded-[10px] px-4 text-[14px] [&_svg:not([class*='size-'])]:size-3.5",
  sm: "h-7 gap-1 rounded-md px-2.5 text-[12.5px] [&_svg:not([class*='size-'])]:size-3.5",
  xs: "h-6 gap-1 rounded-md px-2 text-xs [&_svg:not([class*='size-'])]:size-3",
  icon: "size-8",
  "icon-sm": "size-7 rounded-md",
  "icon-xs": "size-6 rounded-md [&_svg:not([class*='size-'])]:size-3",
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
    "inline-flex shrink-0 items-center justify-center rounded-lg border border-transparent text-[13px] font-medium whitespace-nowrap transition-colors outline-none select-none focus-visible:ring-3 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
    VARIANT[variant],
    SIZE[size],
  );
}

export function Button({ className, variant, size, type = "button", ...props }: ButtonProps) {
  return <button type={type} className={cn(buttonClass(variant, size), className)} {...props} />;
}

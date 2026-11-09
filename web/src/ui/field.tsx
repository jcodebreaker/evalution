import type { ComponentProps } from "react";
import { cn } from "../lib/cn";

const FIELD =
  "w-full min-w-0 rounded-[6px] border border-rule-strong bg-group px-2.5 text-[14px] outline-none transition-colors placeholder:text-ink-3 hover:border-ink-3 focus-visible:border-tint focus-visible:ring-3 focus-visible:ring-tint-soft-2 disabled:opacity-50";

export function Input({ className, ...props }: ComponentProps<"input">) {
  return <input className={cn(FIELD, "h-8", className)} {...props} />;
}

export function Select({ className, ...props }: ComponentProps<"select">) {
  return <select className={cn(FIELD, "h-8", className)} {...props} />;
}

export function Textarea({ className, ...props }: ComponentProps<"textarea">) {
  return <textarea className={cn(FIELD, "min-h-16 py-2 leading-[1.45]", className)} {...props} />;
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-[6px] bg-sunk-2", className)} />;
}

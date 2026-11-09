import { cn } from "../lib/cn";

/** Work in progress: a small spinner and a shimmering label. */
export function LoadingState({ label, className }: { label: string; className?: string }) {
  return (
    <span role="status" className={cn("flex w-fit items-center gap-2.5 text-[14px]", className)}>
      <Spinner />
      <span className="shimmer">{label}</span>
    </span>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={cn(
        "size-3 shrink-0 animate-spin rounded-full border-2 border-ink-2 border-r-transparent",
        className,
      )}
    />
  );
}

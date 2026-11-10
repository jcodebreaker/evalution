import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

/** A section of a test's page: a white card with its title, and a quiet aside to the right. */
export function Card({
  title,
  aside,
  label,
  className,
  testId,
  children,
}: {
  title: string;
  aside?: ReactNode;
  /** Its name to a screen reader, when not its title. */
  label?: string;
  className?: string;
  testId?: string;
  children: ReactNode;
}) {
  return (
    <section
      className={cn(
        "min-w-0 overflow-hidden rounded-2xl border border-rule bg-group shadow-[0_1px_2px_var(--shadow)]",
        className,
      )}
      aria-label={label ?? title}
      data-testid={testId}
    >
      <div className="flex min-h-[54px] flex-wrap items-center gap-x-3 gap-y-1 border-b border-rule px-5 py-2">
        <h2 className="m-0 text-[15px] leading-snug font-semibold">{title}</h2>
        {aside ? <span className="ms-auto text-[13px] text-ink-3">{aside}</span> : null}
      </div>
      {children}
    </section>
  );
}

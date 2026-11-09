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
      className={cn("group-card", className)}
      aria-label={label ?? title}
      data-testid={testId}
    >
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-5 pt-4">
        <h2 className="m-0 text-[16px] leading-snug font-semibold">{title}</h2>
        {aside ? <span className="ms-auto text-[12px] text-ink-3">{aside}</span> : null}
      </div>
      {children}
    </section>
  );
}

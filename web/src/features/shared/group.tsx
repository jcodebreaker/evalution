import { ChevronRight } from "lucide-react";
import type { ComponentProps, ReactNode } from "react";
import { cn } from "../../lib/cn";

/** An inset group: one rounded card, rows split by hairlines. */
export function Group({ className, ...props }: ComponentProps<"div">) {
  return <div className={cn("group-card", className)} {...props} />;
}

export function GroupHeading({ title, aside }: { title: ReactNode; aside?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline gap-2 px-1 pb-1.5">
      <h3 className="m-0 text-[13px] font-semibold">{title}</h3>
      {aside ? (
        <span className="ms-auto flex items-center gap-3 text-[12px] text-ink-3">{aside}</span>
      ) : null}
    </div>
  );
}

/** A folded section: a quiet chevron line that opens what is rarely needed. */
export function Fold({
  title,
  className,
  children,
}: {
  title: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <details className={cn("group/fold", className)}>
      <summary className="flex w-fit cursor-default list-none items-center gap-1.5 rounded-md px-1 py-0.5 text-[12.5px] text-ink-2 hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring [&::-webkit-details-marker]:hidden">
        <ChevronRight
          className="size-3 shrink-0 text-ink-3 transition-transform group-open/fold:rotate-90"
          aria-hidden
        />
        {title}
      </summary>
      <div className="pt-2">{children}</div>
    </details>
  );
}

/** A plain-text link inside a line. */
export function TextLink({ className, ...props }: ComponentProps<"button">) {
  return (
    <button
      type="button"
      className={cn(
        "text-start text-ink-2 underline decoration-ink-3 underline-offset-2 hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring",
        className,
      )}
      {...props}
    />
  );
}

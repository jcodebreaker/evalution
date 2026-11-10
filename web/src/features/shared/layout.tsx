import { ChevronLeft } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import { Button } from "../../ui/button";

const COLUMN = {
  narrow: "max-w-[760px]",
  default: "max-w-[860px]",
  wide: "max-w-[1180px]",
  full: "max-w-[1320px] gap-5",
} as const;

/** The head of a screen, first in its `Page`: its title, then its actions. */
export function Toolbar({ children }: { children: ReactNode }) {
  return <header className="flex flex-wrap items-center gap-x-5 gap-y-3">{children}</header>;
}

export function ToolbarTitle({ title, sub }: { title: ReactNode; sub?: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1">
      <h1 className="m-0 truncate text-[30px] leading-[1.15] font-semibold tracking-[-0.01em] max-sm:text-[24px]">
        {title}
      </h1>
      {sub ? <span className="truncate text-sm text-ink-2">{sub}</span> : null}
    </div>
  );
}

/** The toolbar's way back to the screen this one was opened from. */
export function BackLink({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <Button variant="link" size="sm" className="-ms-1 gap-1" onClick={onClick}>
      <ChevronLeft />
      {label}
    </Button>
  );
}

/** A screen's scrolling body, one readable column. */
export function Page({
  children,
  narrow,
  wide,
  full,
}: {
  children: ReactNode;
  narrow?: boolean;
  wide?: boolean;
  /** The result screens' width, as the mock-up: up to 1320 px. */
  full?: boolean;
}) {
  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <div
        className={cn(
          "mx-auto flex flex-col gap-6 px-8 pt-7 pb-14 max-md:px-4 max-md:pt-5",
          COLUMN[narrow ? "narrow" : full ? "full" : wide ? "wide" : "default"],
        )}
      >
        {children}
      </div>
    </div>
  );
}

export function Centered({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center text-sm text-ink-2">
      {children}
    </div>
  );
}

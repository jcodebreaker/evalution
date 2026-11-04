import { ChevronLeft } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import { Button } from "../../ui/button";

/** The bar on top of a screen. */
export function Toolbar({ children }: { children: ReactNode }) {
  return (
    <header className="flex min-h-[60px] flex-wrap items-center gap-2.5 border-b border-separator px-6 py-2.5 max-md:px-4">
      {children}
    </header>
  );
}

export function ToolbarTitle({ title, sub }: { title: ReactNode; sub?: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <h1 className="m-0 truncate text-[20px] font-semibold tracking-[-0.02em]">{title}</h1>
      {sub ? <span className="truncate text-[12px] text-ink-2">{sub}</span> : null}
    </div>
  );
}

/** The toolbar's way back to the screen this one was opened from. */
export function BackLink({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <Button variant="ghost" size="sm" className="-ms-2 text-ink-2" onClick={onClick}>
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
}: {
  children: ReactNode;
  narrow?: boolean;
  wide?: boolean;
}) {
  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <div
        className={cn(
          "mx-auto flex flex-col gap-6 px-6 pt-6 pb-14 max-md:px-4 max-md:pt-4",
          narrow ? "max-w-[760px]" : wide ? "max-w-[1180px]" : "max-w-[860px]",
        )}
      >
        {children}
      </div>
    </div>
  );
}

export function Centered({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center text-[13.5px] text-ink-2">
      {children}
    </div>
  );
}

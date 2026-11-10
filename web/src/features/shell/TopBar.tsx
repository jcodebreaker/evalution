import { ChevronLeft } from "lucide-react";

/**
 * The bar over every screen: on a test or a run, the way back to its list (a top-level screen's own
 * heading is its title, so there is no breadcrumb), and the run playing now, if any, which opens it.
 */
export function TopBar({
  back,
  live,
}: {
  back: { label: string; onClick: () => void } | null;
  live: { label: string; onOpen: () => void } | null;
}) {
  return (
    <header className="z-10 flex h-[52px] shrink-0 items-center gap-3 bg-window/85 px-8 backdrop-blur-[10px] backdrop-saturate-[1.4] max-md:px-4">
      {back ? (
        <button
          type="button"
          onClick={back.onClick}
          className="inline-flex h-[34px] min-w-0 items-center gap-1.5 rounded-[9px] ps-1.5 pe-2.5 whitespace-nowrap text-ink-2 hover:bg-sunk hover:text-ink focus-visible:outline-2 focus-visible:outline-tint"
        >
          <ChevronLeft className="size-[15px] shrink-0" aria-hidden />
          <span className="truncate">{back.label}</span>
        </button>
      ) : null}
      <span className="flex-1" />
      {live ? (
        <button
          type="button"
          title="A run is playing: open it"
          onClick={live.onOpen}
          className="inline-flex h-[30px] shrink-0 items-center gap-2 rounded-full bg-tint-soft px-3 text-[13px] font-medium whitespace-nowrap text-tint-ink hover:bg-tint-soft-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-tint"
        >
          <i aria-hidden className="size-[7px] shrink-0 animate-pulse rounded-full bg-tint" />
          <span className="max-sm:sr-only">{live.label}</span>
        </button>
      ) : null}
    </header>
  );
}

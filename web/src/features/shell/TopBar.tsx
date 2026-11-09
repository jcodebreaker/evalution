/**
 * The bar over every screen: where the page is, and the run playing now, if any, which opens it.
 */
export function TopBar({
  crumbs,
  live,
}: {
  crumbs: readonly string[];
  live: { label: string; onOpen: () => void } | null;
}) {
  const last = crumbs.length - 1;
  return (
    <header className="z-10 flex h-[52px] shrink-0 items-center gap-3 border-b border-separator bg-window/85 px-8 backdrop-blur-[10px] backdrop-saturate-[1.4] max-md:px-4">
      <nav
        aria-label="Where you are"
        className="flex min-w-0 flex-1 items-center gap-2 overflow-hidden text-[13px] whitespace-nowrap text-ink-3"
      >
        {crumbs.map((crumb, index) => (
          <span key={`${index}:${crumb}`} className="flex min-w-0 items-center gap-2">
            {index ? (
              <span aria-hidden className="text-rule-strong">
                /
              </span>
            ) : null}
            {index === last ? (
              <b aria-current="page" className="truncate font-medium text-ink">
                {crumb}
              </b>
            ) : (
              <span className="truncate">{crumb}</span>
            )}
          </span>
        ))}
      </nav>
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

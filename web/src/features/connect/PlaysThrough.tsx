import type { TargetDetail } from "../../lib/schema";

/** How tests reach the app: its API, its own page by the map in its `page:` block, or that page
 * alone (`adapter: page`). Read only here: the map is learned and approved on the Test environment
 * page, or written by hand. */
export function PlaysThrough({ page }: { page: TargetDetail["page"] }) {
  const way = page?.only ? "Its page only" : page ? "Its page" : "Its API";
  return (
    <div className="flex flex-col gap-1 text-[13px]" data-testid="plays-through">
      <p className="m-0 text-ink-2">
        Plays through: <span className="font-semibold text-foreground">{way}</span>
        {page ? <span className="text-ink-3"> · Set in the target's page: block</span> : null}
      </p>
      {page?.only ? (
        <p className="m-0 text-[12.5px] text-ink-2">
          No API under it: each reply is what the page showed, so a pass is reply only. Nothing it
          stores can be checked.
        </p>
      ) : null}
      {page?.error ? (
        <p className="m-0 text-[12.5px] text-destructive">
          Its page map doesn't read: {page.error}
        </p>
      ) : null}
      {page?.map.length ? (
        <p className="m-0 text-[12.5px] text-ink-2">
          {page.map.map((entry) => `${entry.what}: ${entry.where}`).join("; ")}
        </p>
      ) : null}
    </div>
  );
}

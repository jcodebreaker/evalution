import { CircleAlert } from "lucide-react";
import { cn } from "../../lib/cn";
import type { Health } from "../../lib/schema";
import { Button } from "../../ui/button";
import { useOpenEnvironment } from "../environment/use-isolation";
import { TextLink } from "../shared/group";
import { PageShot, relearnWords } from "../shared/next-step";
import { CARD } from "./card";

/** What the test writer is asked when Home offers to learn the page again. */
export function relearn(target: string, page: NonNullable<Health["page"]>): string {
  return page.status === "broken"
    ? relearnWords(target, page.missing)
    : `The page map of ${target} is out of date: a commit changed ${page.stale_files.join(", ") || "a file it rests on"}. Learn the page again and propose a new map.`;
}

/**
 * Home's word on the page map, as it says an isolation plan is out of date: only when a commit
 * changed a file its evidence cites, or the check before a run (or a try) found the page without
 * what it names, shown as it was then. Offers to learn the page again.
 */
export function PageMapCard({
  target,
  page,
  onAsk,
}: {
  target: string;
  page: Health["page"];
  onAsk: (text: string) => void;
}) {
  const open = useOpenEnvironment();
  if (!page) return null;
  const broken = page.status === "broken";
  return (
    <section
      className={cn(CARD, "flex flex-col gap-3 text-[13px]")}
      aria-label={`How tests reach ${target}`}
      data-testid="page-map-card"
    >
      <h2 className="m-0 flex items-center gap-2 text-[15px] font-semibold">
        <CircleAlert
          className={cn("size-4", broken ? "text-destructive" : "text-warning")}
          aria-hidden
        />
        How tests reach {target}
      </h2>
      <div className="flex flex-wrap items-start gap-3">
        {broken && page.shot ? (
          <PageShot
            src={page.shot}
            title={`${target}'s page, without ${page.missing.join(", ")}`}
            alt={`${target}'s page as the check found it`}
          />
        ) : null}
        <p className="m-0 max-w-[68ch] min-w-0 flex-1">
          {broken
            ? `The page map no longer matches ${target}'s page: it has no ${page.missing.join(", ")}. Runs through the page stop until it's learned again.`
            : `The page map is out of date: a commit changed ${page.stale_files.join(", ") || "a file it rests on"}. The next run checks it on evalution's copy first, without sending a message.`}
        </p>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant={broken ? "default" : "secondary"}
          onClick={() => onAsk(relearn(target, page))}
        >
          Re-learn the page
        </Button>
        {open ? (
          <TextLink className="text-[12.5px]" onClick={open}>
            Details
          </TextLink>
        ) : null}
      </div>
    </section>
  );
}

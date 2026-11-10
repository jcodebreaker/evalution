import { useState } from "react";
import { cn } from "../../lib/cn";
import type { MissingPiece } from "../../lib/schema";
import { Button } from "../../ui/button";
import { PictureDialog } from "../../ui/dialog";
import { useOpenEnvironment } from "../environment/use-isolation";
import { InstallPieces } from "./ui-install";

export { RUN_IT } from "./ui-install";

/** What the test writer is asked to learn the page again, saying what the page lacked. */
export function relearnWords(target: string, missing: readonly string[]): string {
  return `The page map of ${target} no longer matches its page: it has no ${missing.join(", ")}. Learn the page again and propose a new map.`;
}

/**
 * A piece UI testing lacks (ADR 0010), in one sentence, and the one action that fixes it: Set up
 * testing for a copy of the app; else Install, which installs it on the server's computer with
 * its progress where the server can, the exact command to copy staying as the way by hand.
 * `onLeave`: called before the page moves to the Test environment (a dialog closes, say);
 * `onInstalled`: once an install leaves nothing missing.
 */
export function MissingPieceNote({
  piece,
  onLeave,
  onInstalled,
  className,
}: {
  piece: MissingPiece;
  onLeave?: () => void;
  onInstalled?: () => void;
  className?: string;
}) {
  const open = useOpenEnvironment();
  return (
    <div
      className={cn("flex min-w-0 flex-col gap-2 text-[13px]", className)}
      data-testid="missing-piece"
    >
      <p className="m-0 max-w-[68ch]">{piece.said}</p>
      {piece.command === null ? (
        open ? (
          <div>
            <Button
              size="sm"
              onClick={() => {
                onLeave?.();
                open();
              }}
            >
              Set up testing
            </Button>
          </div>
        ) : (
          <p className="m-0 text-ink-2">Set up testing on the Test environment page makes one.</p>
        )
      ) : (
        <InstallPieces pieces={[piece]} onInstalled={onInstalled} />
      )}
    </div>
  );
}

/** A screenshot of the page, small, that opens large. */
export function PageShot({ src, title, alt }: { src: string; title: string; alt: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        aria-label={`Enlarge: ${alt}`}
        onClick={() => setOpen(true)}
        className="shrink-0 self-start overflow-hidden rounded-lg ring-1 ring-separator hover:ring-tint focus-visible:outline-2 focus-visible:outline-tint"
      >
        <img
          src={src}
          alt={alt}
          loading="lazy"
          className="block h-[90px] w-[150px] object-cover object-top"
        />
      </button>
      {open ? (
        <PictureDialog onClose={() => setOpen(false)} title={title} src={src} alt={alt} />
      ) : null}
    </>
  );
}

/**
 * A page map the page no longer matches: what wasn't found, the page as it was, and Re-learn the
 * page. `onRelearn`: asks the test writer to learn it again; none where no one can be asked.
 */
export function BrokenMapNote({
  target,
  missing,
  shot,
  onRelearn,
}: {
  target: string;
  missing: readonly string[];
  shot: string | null;
  onRelearn?: (text: string) => void;
}) {
  return (
    <section
      className="flex flex-wrap items-start gap-3 rounded-[10px] bg-unjudged-soft px-4 py-3.5 text-sm"
      aria-label="The page map no longer matches the page"
      data-testid="broken-map"
    >
      {shot ? (
        <PageShot
          src={shot}
          title={`${target}'s page, without ${missing.join(", ")}`}
          alt={`${target}'s page as it was`}
        />
      ) : null}
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <p className="m-0 max-w-[68ch]">
          {`The page map no longer matches ${target}'s page: it has no ${missing.join(", ")}.`}
        </p>
        {onRelearn ? (
          <div>
            <Button size="sm" onClick={() => onRelearn(relearnWords(target, missing))}>
              Re-learn the page
            </Button>
          </div>
        ) : null}
      </div>
    </section>
  );
}

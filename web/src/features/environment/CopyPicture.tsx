import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import { Status, StatusDot } from "../../ui/status";
import { type Block, copyName } from "./words";

/** A block on the copy as it is now: checked by the proof, or marked while it's proven. */
export type ShownBlock = Block & { checked: boolean; marking: boolean };

/** How the copy looks: green when proven, amber out of date, red not isolated. */
export type Tone = "plain" | "ready" | "stale" | "leaky";

const LANE = "mb-2 text-[12px] text-ink-3";

function BlockCard({ block, animate }: { block: ShownBlock; animate: boolean }) {
  const person = block.kind === "person";
  return (
    <li
      className={cn(
        "flex min-w-0 items-center gap-3 rounded-[10px] border border-separator bg-group px-3 py-2.5",
        person && "col-span-full",
        animate && "block-in",
      )}
      data-testid={`block-${block.key}`}
    >
      {person && block.face ? (
        <span className="grid size-[30px] shrink-0 place-items-center rounded-[7px] bg-tint-soft text-[13px] font-semibold text-tint-ink">
          {block.face}
        </span>
      ) : null}
      <span className="flex min-w-0 flex-col">
        <b className="truncate font-medium">{block.title}</b>
        <span className="truncate text-[12px] text-ink-3">{block.sub}</span>
      </span>
      <span className="ms-auto flex shrink-0 items-center gap-2">
        {block.marking ? (
          <i
            className="marker-pulse size-2 rounded-full bg-unjudged"
            title="Marked while it's proven"
            aria-hidden
          />
        ) : null}
        {block.checked ? (
          <StatusDot state="pass" label="Checked" />
        ) : (
          <span aria-hidden className="size-2" />
        )}
      </span>
    </li>
  );
}

/**
 * Your app, faded and locked, beside the test copy evalution builds: one block for the app on its
 * own ports, its traces, each store it gets new, and the test person.
 */
export function CopyPicture({
  target,
  yours,
  blocks,
  tone,
  badge,
  animate,
  empty,
}: {
  target: string;
  /** The app's own places, as its side names them: "Your database". */
  yours: readonly string[];
  blocks: readonly ShownBlock[];
  tone: Tone;
  /** A word on the copy's state beside its name: "Out of date". */
  badge?: ReactNode;
  /** Blocks come in as they appear (while it's set up), not all at once. */
  animate: boolean;
  /** Said inside the copy while it has no blocks. */
  empty?: string;
}) {
  return (
    <div className="grid grid-cols-[minmax(0,180px)_40px_minmax(0,1fr)] items-stretch max-sm:grid-cols-1">
      <div className="flex flex-col">
        <div className={LANE}>Your app</div>
        <div
          className="flex flex-1 flex-col gap-2 rounded-[12px] border border-dashed border-rule-strong p-3 opacity-75"
          data-testid="your-app"
        >
          {[target, ...yours].map((name) => (
            <div
              key={name}
              className="truncate rounded-[8px] bg-sunk px-2.5 py-2 font-medium text-ink-2"
            >
              {name}
            </div>
          ))}
          <div className="mt-0.5 text-[12px] text-ink-3">Never touched</div>
        </div>
      </div>
      <div
        aria-hidden
        className="grid place-items-center text-[18px] text-ink-3 max-sm:rotate-90 max-sm:py-1.5"
      >
        →
      </div>
      <div className="flex min-w-0 flex-col">
        <div className={cn(LANE, "flex items-center gap-2")}>
          Test copy
          {badge}
        </div>
        <div
          className={cn(
            "relative flex min-h-[200px] flex-1 flex-col rounded-[12px] border p-3 transition-colors duration-500",
            tone === "ready" && "border-pass/50 bg-pass-soft/40",
            tone === "stale" && "border-unjudged",
            tone === "leaky" && "border-fail",
            tone === "plain" && "border-separator",
          )}
          data-testid="test-copy"
          data-tone={tone}
        >
          <div className="mb-2.5 font-mono text-[12.5px] text-ink-3">{copyName(target)}</div>
          {blocks.length ? (
            <ul className="m-0 grid list-none grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-2.5 p-0">
              {blocks.map((block) => (
                <BlockCard key={block.key} block={block} animate={animate} />
              ))}
            </ul>
          ) : (
            <p className="m-auto text-[13px] text-ink-3">{empty}</p>
          )}
          {tone === "ready" ? (
            <div className="mt-2.5 flex justify-end">
              <Status state="pass" className="font-medium text-ink">
                Starts fresh, proven
              </Status>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

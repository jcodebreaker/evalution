import { ArrowRight, Database, Lock, User } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import { Status, StatusDot } from "../../ui/status";
import { type Block, copyName } from "./words";

/** A block on the copy as it is now: checked by the proof, or marked while it's proven. */
export type ShownBlock = Block & { checked: boolean; marking: boolean };

/** How the copy looks: green when proven, amber out of date, red not isolated. */
export type Tone = "plain" | "ready" | "stale" | "leaky";

const ROW = "flex h-10 min-w-0 items-center gap-2.5 rounded-[10px] border border-rule px-3 text-sm";

function BlockRow({ block, animate }: { block: ShownBlock; animate: boolean }) {
  const person = block.kind === "person";
  return (
    <li className={cn(ROW, "bg-group", animate && "block-in")} data-testid={`block-${block.key}`}>
      {block.marking ? (
        <i
          className="marker-pulse size-2.5 shrink-0 rounded-full bg-unjudged"
          title="Marked while it's proven"
          aria-hidden
        />
      ) : block.checked ? (
        <StatusDot state="pass" label="Checked" className="size-2.5" />
      ) : (
        <i aria-hidden className="size-2.5 shrink-0 animate-pulse rounded-full bg-tint" />
      )}
      {person && block.face ? (
        <span className="grid size-6 shrink-0 place-items-center rounded-full bg-tint-soft text-sm font-semibold text-tint-ink">
          {block.face}
        </span>
      ) : person ? (
        <User aria-hidden className="size-[15px] shrink-0 text-ink-3" />
      ) : block.kind === "store" ? (
        <Database aria-hidden className="size-[15px] shrink-0 text-ink-3" />
      ) : null}
      <b className="min-w-0 truncate font-normal">{block.title}</b>
      <small className="ms-auto truncate ps-2 font-mono text-[13px] text-ink-3">{block.sub}</small>
    </li>
  );
}

/**
 * Your app, dashed and locked, beside the test copy evalution builds: one block for the app on
 * its own ports, its traces, each store it gets new, and the test person, drawn as they land.
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
    <div className="grid grid-cols-[minmax(0,0.8fr)_36px_minmax(0,1.3fr)] items-stretch max-md:grid-cols-1">
      <div
        className="flex min-h-[176px] flex-col gap-2 rounded-[14px] border-[1.5px] border-dashed border-rule-strong p-3.5"
        data-testid="your-app"
      >
        <h3 className="m-0 mb-0.5 flex items-center gap-2 text-sm font-medium text-ink-3">
          <Lock aria-hidden className="size-[15px]" />
          Your app · never touched
        </h3>
        {[target, ...yours].map((name) => (
          <div key={name} className={cn(ROW, "bg-sunk text-ink-3")}>
            {name === target ? null : <Database aria-hidden className="size-[15px] shrink-0" />}
            <span className="truncate">{name}</span>
          </div>
        ))}
      </div>
      <div
        aria-hidden
        className="grid place-items-center text-ink-3 max-md:h-[30px] max-md:rotate-90"
      >
        <ArrowRight className="size-[18px]" />
      </div>
      <div
        className={cn(
          "relative flex min-h-[176px] flex-col gap-2 rounded-[14px] border-2 p-3.5 transition-colors duration-500",
          tone === "ready" && "border-pass bg-pass-soft",
          tone === "stale" && "border-unjudged bg-unjudged-soft",
          tone === "leaky" && "border-fail bg-fail-soft",
          tone === "plain" && "border-tint bg-tint-soft",
        )}
        data-testid="test-copy"
        data-tone={tone}
      >
        <h3 className="m-0 mb-0.5 flex flex-wrap items-center gap-2 text-sm font-medium text-ink-3">
          Test copy ·<span className="font-mono">{copyName(target)}</span>
          {badge}
        </h3>
        {blocks.length ? (
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {blocks.map((block) => (
              <BlockRow key={block.key} block={block} animate={animate} />
            ))}
          </ul>
        ) : (
          <p className="m-auto text-sm text-ink-3">{empty}</p>
        )}
        {tone === "ready" ? (
          <div className="mt-auto flex justify-end pt-1">
            <Status state="pass" className="text-sm font-medium text-ink">
              Starts fresh, proven
            </Status>
          </div>
        ) : null}
      </div>
    </div>
  );
}

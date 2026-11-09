import {
  Activity,
  ArrowRight,
  Brain,
  Check,
  Database,
  Folder,
  HardDrive,
  Lock,
  Monitor,
  ShieldCheck,
  UserRound,
} from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import { type Block, copyName } from "./words";

/** A block on the copy as it is now: checked by the proof, or marked while it's proven. */
export type ShownBlock = Block & { checked: boolean; marking: boolean };

/** How the copy looks: green when proven, amber out of date, red not isolated. */
export type Tone = "plain" | "ready" | "stale" | "leaky";

const LANE = "mb-2 text-[11px] font-semibold tracking-[0.06em] text-ink-3 uppercase";

function storeIcon(kind: string | undefined) {
  if (kind === "memory" || kind === "vector") return <Brain className="size-4" aria-hidden />;
  if (kind === "volume") return <HardDrive className="size-4" aria-hidden />;
  if (kind === "dir") return <Folder className="size-4" aria-hidden />;
  return <Database className="size-4" aria-hidden />;
}

function BlockCard({ block, animate }: { block: ShownBlock; animate: boolean }) {
  const person = block.kind === "person";
  const icon =
    block.kind === "app" ? (
      <Monitor className="size-4" aria-hidden />
    ) : block.kind === "traces" ? (
      <Activity className="size-4" aria-hidden />
    ) : block.kind === "store" ? (
      storeIcon(block.of)
    ) : (
      (block.face ?? <UserRound className="size-4" aria-hidden />)
    );
  return (
    <li
      className={cn(
        "flex min-w-0 items-center gap-2.5 rounded-[11px] border border-border bg-group p-3",
        person && "col-span-full",
        animate && "block-in",
      )}
      data-testid={`block-${block.key}`}
    >
      <span
        className={cn(
          "relative grid size-9 shrink-0 place-items-center",
          person
            ? "rounded-full bg-up text-[14px] font-bold text-success"
            : "rounded-[10px] bg-selection-strong text-tint",
        )}
      >
        {icon}
        {block.marking ? (
          <i
            className="marker-pulse absolute -top-[3px] -right-[3px] size-2.5 rounded-full bg-warning"
            aria-hidden
          />
        ) : null}
      </span>
      <span className="flex min-w-0 flex-col">
        <b className="truncate font-semibold">{block.title}</b>
        <span className="truncate text-[12.5px] text-ink-3">{block.sub}</span>
      </span>
      <Check
        className={cn(
          "ms-auto size-4 shrink-0 text-success transition-opacity duration-300",
          block.checked ? "opacity-100" : "opacity-0",
        )}
        aria-label={block.checked ? "Checked" : undefined}
        aria-hidden={!block.checked}
      />
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
          className="flex flex-1 flex-col gap-2 rounded-xl border-[1.5px] border-dashed border-input p-3 opacity-75"
          data-testid="your-app"
        >
          {[target, ...yours].map((name) => (
            <div
              key={name}
              className="truncate rounded-[9px] bg-bubble px-2.5 py-2 font-medium text-ink-2"
            >
              {name}
            </div>
          ))}
          <div className="mt-0.5 flex items-center gap-1.5 text-[11.5px] text-ink-3">
            <Lock className="size-3.5" aria-hidden />
            Never touched
          </div>
        </div>
      </div>
      <div className="grid place-items-center text-ink-3 max-sm:rotate-90 max-sm:py-1.5">
        <ArrowRight className="size-5" aria-hidden />
      </div>
      <div className="flex min-w-0 flex-col">
        <div className={cn(LANE, "flex items-center gap-2")}>
          Test copy
          {badge}
        </div>
        <div
          className={cn(
            "relative flex min-h-[200px] flex-1 flex-col rounded-xl border-[1.5px] p-3 transition-colors duration-500",
            tone === "ready" && "border-success bg-up/35",
            tone === "stale" && "border-warning",
            tone === "leaky" && "border-destructive",
            tone === "plain" && "border-border",
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
            <div className="mt-2.5 flex items-center justify-end gap-1.5 font-semibold text-success">
              <ShieldCheck className="size-4" aria-hidden />
              Starts fresh, proven
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

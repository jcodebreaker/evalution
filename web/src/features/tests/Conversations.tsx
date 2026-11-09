import { api } from "../../lib/api";
import { cn } from "../../lib/cn";
import { when } from "../../lib/format";
import type { Source } from "../../lib/schema";
import { useLoad } from "../../lib/use-load";

/** The app's recent conversations, newest first. */
export function Conversations({
  target,
  onPick,
}: {
  target: string;
  onPick: (source: Source) => void;
}) {
  const [load] = useLoad(`sources:${target}`, () => api.sources(target));
  const quiet = "m-0 border-t border-separator px-3.5 py-3 text-[14px] text-ink-2";
  if (load.status === "loading") return <p className={cn(quiet, "shimmer")}>Loading…</p>;
  if (load.status !== "ready") return <p className={quiet}>Couldn't load conversations.</p>;
  if (!load.data.length) return <p className={quiet}>No recent conversations.</p>;
  return (
    <div className="max-h-72 overflow-auto border-t border-separator">
      {load.data.map((source) => (
        <button
          key={`${source.session_id}:${source.round_id}`}
          type="button"
          onClick={() => onPick(source)}
          className="flex w-full items-baseline gap-3 border-separator px-3.5 py-2.5 text-start text-[13.5px] not-first:border-t hover:bg-selection focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring"
        >
          <span className="min-w-0 flex-1 truncate">{source.ask}</span>
          <span className="shrink-0 text-[12.5px] text-ink-3 tabular-nums">{when(source.at)}</span>
        </button>
      ))}
    </div>
  );
}

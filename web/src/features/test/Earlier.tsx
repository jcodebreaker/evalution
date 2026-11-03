import { cn } from "../../lib/cn";
import { when } from "../../lib/format";
import type { Verdict } from "../../lib/schema";
import { VERDICT_CELL } from "../shared/marks";

/** Every result this test has had, oldest first, one square per run; the shown one ringed. */
export function Earlier({
  history,
  shown,
  runLabel,
  onRunResult,
}: {
  history: { run: string; at: number; verdict: Verdict | null }[];
  shown: string | null;
  runLabel: (run: string) => string;
  onRunResult: (run: string) => void;
}) {
  const ordered = [...history].sort((a, b) => a.at - b.at).slice(-20);
  return (
    <div className="flex flex-wrap items-center gap-2 px-1 text-[12px] text-ink-3">
      <span>Earlier results</span>
      <span className="flex flex-wrap gap-1">
        {ordered.map((entry) => (
          <button
            key={entry.run}
            type="button"
            title={`${runLabel(entry.run)} · ${when(entry.at)}`}
            aria-label={`${runLabel(entry.run)}, ${entry.verdict ?? "no result"}`}
            onClick={() => onRunResult(entry.run)}
            className={cn(
              "size-3 rounded-[3px] focus-visible:outline-2 focus-visible:outline-ring",
              entry.verdict ? VERDICT_CELL[entry.verdict] : "ring-1 ring-inset ring-separator",
              entry.run === shown && "outline-2 outline-offset-1 outline-foreground",
            )}
          />
        ))}
      </span>
    </div>
  );
}

import { ChartColumn } from "lucide-react";
import { live } from "../../lib/format";
import type { RunSummary } from "../../lib/schema";
import { Spinner } from "../../ui/loading";
import { Group } from "../shared/group";

export const ROW_LINK =
  "flex w-full items-center gap-2.5 px-3.5 py-2.5 text-start text-[14px] hover:bg-sunk focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-tint";

/** A run the writer started or reviewed: how far it is, or how it ended. */
export function RunLink({
  label,
  summary,
  onClick,
}: {
  label: string;
  summary: RunSummary | null;
  onClick: () => void;
}) {
  let state: string | null = null;
  if (summary?.analyzing) state = "Analyzing";
  else if (summary?.status === "running")
    state = `${summary.progress.done} of ${summary.progress.total} tries`;
  else if (summary?.status === "done")
    state = `${summary.totals.passed} of ${summary.totals.cases} pass`;
  else if (summary?.status === "stopped") state = "Stopped";
  else if (summary?.status === "failed") state = "Didn't finish";
  return (
    <Group>
      <button type="button" onClick={onClick} className={ROW_LINK}>
        {summary && live(summary) ? (
          <Spinner />
        ) : (
          <ChartColumn className="size-3.5 shrink-0 text-ink-3" aria-hidden />
        )}
        <span className="min-w-0 flex-1 truncate font-medium">{label}</span>
        {state ? (
          <span className="shrink-0 text-[12.5px] text-ink-3 tabular-nums">{state}</span>
        ) : null}
      </button>
    </Group>
  );
}

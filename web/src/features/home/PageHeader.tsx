import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import { ago } from "../../lib/format";
import type { Health, RunSummary } from "../../lib/schema";
import { Button } from "../../ui/button";
import { versionChip } from "../runs/Version";

export type Chip = { id: string; text: string; title?: string; tone?: "tint" | "mono" };

/** How the newest run's tries were kept apart, else how the plan stands now: only what is known. */
function playsOn(run: RunSummary | null, isolation: Health["isolation"]): string | null {
  const how = run?.isolation?.how ?? (isolation?.status === "proven" ? "proven" : null);
  if (how === "proven") return "Plays on evalution's copy, reset before every try";
  if (how === "identity") return "Each try as a new person, nothing reset";
  if (how === "none") return "Tries aren't kept apart";
  return null;
}

const PLAYS: Record<NonNullable<RunSummary["plays"]>, string> = {
  api: "Through its API",
  page: "Through its page",
  page_only: "Through its page alone",
};

/** The header's chips: how tests play, the code the newest run played, and how it reached the
 * app; a chip only where a run or the plan says it. */
export function headerChips(run: RunSummary | null, isolation: Health["isolation"]): Chip[] {
  const chips: Chip[] = [];
  const how = playsOn(run, isolation);
  if (how) chips.push({ id: "how", text: how });
  const code = versionChip(run?.version);
  if (code)
    chips.push({ id: "code", text: code, title: run?.version?.said || undefined, tone: "mono" });
  if (run?.plays)
    chips.push({
      id: "plays",
      text: PLAYS[run.plays],
      tone: run.plays === "api" ? undefined : "tint",
    });
  return chips;
}

/** The Overview's header: the day and the last run, how the app is doing, the facts of how it's
 * tested, and Write a test and Run all. */
export function PageHeader({
  target,
  lastRunAt,
  chips,
  runAll,
  onWrite,
}: {
  target: string;
  lastRunAt: number | null;
  chips: Chip[];
  runAll: ReactNode;
  onWrite: () => void;
}) {
  const today = new Date().toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
  });
  return (
    <header className="flex flex-wrap items-start gap-5">
      <div className="min-w-0 flex-[1_1_420px]">
        <p className="m-0 mb-2 text-[13px] text-ink-3">
          {today}
          {lastRunAt ? ` · last run ${ago(lastRunAt)}` : ""}
        </p>
        <h1 className="m-0 text-[30px] leading-[1.15] font-semibold tracking-[-0.01em] [overflow-wrap:anywhere]">
          How {target} is doing
        </h1>
        {chips.length ? (
          <div className="mt-3 flex flex-wrap gap-2" data-testid="header-chips">
            {chips.map((chip) => (
              <span
                key={chip.id}
                title={chip.title}
                className={cn(
                  "inline-flex h-6 items-center rounded-full border border-separator bg-group px-2 text-[12px] whitespace-nowrap text-ink-2",
                  chip.tone === "mono" && "font-mono text-[11px]",
                  chip.tone === "tint" && "border-tint-soft-2 bg-tint-soft text-tint-ink",
                )}
              >
                {chip.text}
              </span>
            ))}
          </div>
        ) : null}
      </div>
      <div className="flex flex-wrap gap-2 pt-1">
        <Button variant="secondary" onClick={onWrite}>
          Write a test
        </Button>
        {runAll}
      </div>
    </header>
  );
}

import { Sparkles } from "lucide-react";
import type { ReactNode } from "react";
import { ago } from "../../lib/format";
import type { Health, RunSummary } from "../../lib/schema";
import { Button } from "../../ui/button";
import { versionChip } from "../runs/Version";
import { Chip as Fact, PageHead } from "../shared/kit";

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

/** The Overview's header: how the app is doing, the facts of how it's tested and when it last
 * ran, as chips, and New test and Run all. */
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
  return (
    <PageHead
      title={`How ${target} is doing`}
      chips={
        chips.length || lastRunAt ? (
          <span className="contents" data-testid="header-chips">
            {chips.map((chip) => (
              <Fact
                key={chip.id}
                title={chip.title}
                mono={chip.tone === "mono"}
                tone={chip.tone === "tint" ? "tint" : chip.id === "how" ? "ok" : "plain"}
              >
                {chip.text}
              </Fact>
            ))}
            {lastRunAt ? <Fact>{`Last run ${ago(lastRunAt)}`}</Fact> : null}
          </span>
        ) : null
      }
      actions={
        <>
          <Button variant="secondary" size="lg" onClick={onWrite}>
            <Sparkles />
            New test
          </Button>
          {runAll}
        </>
      }
    />
  );
}

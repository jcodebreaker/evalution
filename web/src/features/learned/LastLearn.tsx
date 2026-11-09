import { count, tokens } from "../../lib/format";
import type { AgentSession, LearnStage, TurnUsage } from "../../lib/schema";

/** The server's stage names, as a person reads them. */
const NAMES: Record<string, string> = {
  "code map": "Code map",
  read: "Read",
  modules: "Folder summaries",
  areas: "Areas",
  overview: "Overview",
  other: "Other",
};

/** A stage's time: seconds up to five minutes, minutes past. */
function took(seconds: number): string {
  return seconds < 300 ? `${Math.round(seconds)} s` : `${Math.round(seconds / 60)} min`;
}

/** One stage: "Read 26 min, 48 calls, 1.2M tokens"; a stage with no model call, its time only. */
export function stageText(stage: LearnStage): string {
  const name = NAMES[stage.stage] ?? stage.stage;
  const spent = stage.tokens_in + stage.tokens_out;
  const calls = stage.calls
    ? `, ${count(stage.calls, "call")}${spent ? `, ${tokens(spent)} tokens` : ""}`
    : "";
  return `${name} ${took(stage.seconds)}${calls}`;
}

/** The newest learn measured by stage among *usage*: one that found nothing changed is passed. */
export function lastLearn(usage: readonly TurnUsage[]): TurnUsage | null {
  const learns = usage.filter(
    (turn) =>
      turn.learn &&
      turn.stages.length &&
      (turn.calls || turn.stages.some((stage) => stage.stage !== "other")),
  );
  return learns.at(-1) ?? null;
}

/** What the last learn took, stage by stage, in one quiet line. */
export function LastLearn({ session }: { session: AgentSession | null }) {
  const found = session ? lastLearn(session.usage) : null;
  if (!found) return null;
  return (
    <p className="m-0 px-1 text-[12.5px] text-ink-3">
      <span className="text-ink-2">Last learn:</span> {found.stages.map(stageText).join(" · ")}
    </p>
  );
}

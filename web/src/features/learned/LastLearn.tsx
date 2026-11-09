import { count, tokens } from "../../lib/format";
import type { AgentSession, LearnStage, TurnUsage } from "../../lib/schema";
import { LearnAreaButton, type LearnAreaFn } from "./LearnArea";

/** The server's stage names, as a person reads them. */
const NAMES: Record<string, string> = {
  "code map": "Code map",
  skeleton: "Skeleton",
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

/** What the last learn took, stage by stage, in one quiet line; then the areas it listed and
 * didn't learn (nothing in them the app uses), each with "Learn this area" (*onLearnArea*),
 * unless the page lists the areas itself (*areasShown*). */
export function LastLearn({
  session,
  onLearnArea,
  areasShown = false,
}: {
  session: AgentSession | null;
  onLearnArea?: LearnAreaFn;
  areasShown?: boolean;
}) {
  const found = session ? lastLearn(session.usage) : null;
  const listed = areasShown
    ? []
    : (session?.learn?.areas ?? []).filter((area) => area.state === "listed");
  if (!found && !listed.length) return null;
  return (
    <div className="flex flex-col gap-1.5 px-1">
      {found ? (
        <p className="m-0 text-[12.5px] text-ink-3">
          <span className="text-ink-2">Last learn:</span> {found.stages.map(stageText).join(" · ")}
        </p>
      ) : null}
      {listed.length ? (
        <div className="flex flex-col gap-1">
          <p className="m-0 text-[12.5px] text-ink-3">
            Not learned, nothing in {listed.length === 1 ? "it" : "them"} the app uses:
          </p>
          <ul className="m-0 flex list-none flex-col gap-1 p-0">
            {listed.map((area) => (
              <li
                key={area.id}
                aria-label={area.id}
                className="flex min-h-7 flex-wrap items-center justify-between gap-x-3 text-[12.5px]"
              >
                <span className="min-w-0 truncate font-mono text-ink-2">
                  {area.id}{" "}
                  <span className="font-sans text-ink-3">· {count(area.files, "file")}</span>
                </span>
                {onLearnArea ? <LearnAreaButton area={area.id} onLearn={onLearnArea} /> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

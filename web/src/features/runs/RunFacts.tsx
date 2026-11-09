import type { RunSummary } from "../../lib/schema";
import { Card, CardHead, Fact, Facts } from "./parts";
import { CodeLines, playedWords } from "./Version";

/** How a run's tries were kept apart, in a few words: "Clean copy · plan v2". */
export function copyWords(isolation: RunSummary["isolation"]): string | null {
  if (!isolation) return null;
  if (isolation.how === "proven") {
    return isolation.plan_version === null
      ? "Clean copy"
      : `Clean copy · plan v${isolation.plan_version}`;
  }
  if (isolation.how === "identity") return "Identity only";
  return "Not isolated";
}

/** How a run reached the app, in words: "Through the page, one at a time". */
export function playsWords(plays: RunSummary["plays"]): string | null {
  if (plays === "page") return "Through the page, one at a time";
  if (plays === "page_only") return "Through the page alone, one at a time";
  if (plays === "api") return "Through the API";
  return null;
}

/** The run's details as a key–value list: the app, the tests, the code and what played of it,
 * how its tries were kept apart and how it reached the app. Each only where the run says it. */
export function RunFacts({
  run,
  scopeLabel,
}: {
  run: Pick<
    RunSummary,
    | "target"
    | "set"
    | "scope"
    | "totals"
    | "version"
    | "isolation"
    | "plays"
    | "environment"
    | "prompt"
  >;
  scopeLabel: string;
}) {
  const tests =
    scopeLabel === "All tests" || (!scopeLabel && run.scope === "all")
      ? `${run.set} · all ${run.totals.cases}`
      : scopeLabel
        ? `${run.set} · ${scopeLabel}`
        : run.set;
  const played = playedWords(run.version);
  const copy = copyWords(run.isolation);
  const plays = playsWords(run.plays);
  return (
    <Card data-testid="run-details">
      <CardHead title="Details" />
      <div className="px-5 pt-3 pb-5">
        <Facts>
          {run.target ? <Fact label="App">{run.target}</Fact> : null}
          <Fact label="Tests">{tests}</Fact>
          {run.version?.said ? (
            <Fact label="Code">
              <CodeLines version={run.version} className="items-end text-end" />
            </Fact>
          ) : null}
          {played ? (
            <Fact label="What played">
              <span className="font-mono text-[12px]">{played.join(", ")}</span>
            </Fact>
          ) : null}
          {copy ? <Fact label="Copy">{copy}</Fact> : null}
          {plays ? <Fact label="Plays">{plays}</Fact> : null}
          {run.environment.name || run.environment.where === "hosted" ? (
            <Fact label="Where">{run.environment.name ?? "Hosted"}</Fact>
          ) : null}
          {run.prompt ? (
            <Fact label="Prompt">
              <span className="font-mono text-[12px]">{run.prompt}</span>
            </Fact>
          ) : null}
        </Facts>
      </div>
    </Card>
  );
}

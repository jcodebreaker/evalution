import { useState } from "react";
import type { Scope } from "../../lib/api";
import { api } from "../../lib/api";
import { cn } from "../../lib/cn";
import { count } from "../../lib/format";
import type { Health } from "../../lib/schema";
import { Button } from "../../ui/button";
import { Spinner } from "../../ui/loading";
import { RunAll } from "../runs/RunAll";
import { TextLink } from "../shared/group";
import { withCost } from "../shared/KnowledgeUpdate";
import { CARD } from "./card";

/** What changed in the code since it was last tested, and the tests to run for it. */
export function ChangeCard({
  change,
  canRead,
  readUsd,
  onRead,
  scope,
  going,
  onRun,
  onWrite,
  onGoing,
}: {
  change: NonNullable<Health["code_change"]>;
  canRead: boolean;
  readUsd: number | null;
  onRead: () => void;
  scope: Scope;
  going: boolean;
  onRun: (set: string, cases: readonly string[]) => void;
  onWrite: (text: string) => void;
  onGoing: () => void;
}) {
  const { areas, tests, uncovered, summary, file_count: files } = change;
  const [reading, setReading] = useState(false);
  const read = () => {
    setReading(true);
    api
      .readChange(scope.target)
      .then(onRead, () => undefined)
      .finally(() => setReading(false));
  };
  // One run is one test set: this one first, the rest once it has run.
  const set = tests.some((test) => test.set === scope.set) ? scope.set : tests[0]?.set;
  const cases = tests.filter((test) => test.set === set).map((test) => test.case);
  return (
    <section className={cn(CARD, "flex flex-wrap items-center gap-x-6 gap-y-3")}>
      <div className="flex min-w-60 flex-1 flex-col gap-1">
        <h2 className="m-0 text-[17px] font-bold" title={summary || undefined}>
          Code changed since the last run: {areas.length ? areas.join(", ") : count(files, "file")}
        </h2>
        <p className="m-0 text-[13px] text-ink-2">
          {tests.length ? `${count(tests.length, "test")} affected` : null}
          {tests.length && uncovered.length ? " · " : null}
          {uncovered.length ? (
            <>
              {uncovered.length === 1
                ? "1 area has no test: "
                : `${uncovered.length} areas have no test: `}
              {uncovered.map((area, index) => (
                <span key={area}>
                  {index ? ", " : null}
                  <TextLink onClick={() => onWrite(writeAsk(area, summary))}>{area}</TextLink>
                </span>
              ))}
            </>
          ) : null}
        </p>
      </div>
      {!change.read && canRead ? (
        <Button variant="secondary" disabled={reading} onClick={read}>
          {reading ? <Spinner /> : null}
          {withCost("Read this change", readUsd)}
        </Button>
      ) : null}
      {set && cases.length ? (
        <RunAll
          label={cases.length === tests.length ? "Run them" : `Run ${count(cases.length, "test")}`}
          going={going}
          onGoing={onGoing}
          onRun={() => onRun(set, cases)}
        />
      ) : null}
    </section>
  );
}

/** The ask an area with no test puts in the test writer's reply field. */
function writeAsk(area: string, summary: string): string {
  return `Propose a test for ${area}.${summary ? ` What changed: ${summary}` : ""}`;
}

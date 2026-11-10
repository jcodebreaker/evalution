import { useEffect, useMemo, useRef } from "react";
import type { Scope } from "../../lib/api";
import { api } from "../../lib/api";
import { count, live, playing, runNumbers } from "../../lib/format";
import type { Place } from "../../lib/router";
import type { RunSummary } from "../../lib/schema";
import { POLL_MS, ready, useLoad, useVisiblePoll } from "../../lib/use-load";
import { isBenchmark } from "../benchmarks/BenchmarksView";
import type { Card } from "../learned/ProjectCard";

/** What the page is looking at: the app and test set of `place`, their tests and runs. */
export function useWorkspace(place: Place) {
  // App and test set: from the URL, else the first of each.
  const [targets, reloadTargets] = useLoad("targets", () => api.targets());
  const targetList = ready(targets) ?? [];
  const target = place.target ?? targetList[0]?.name ?? null;
  const [sets, reloadSets] = useLoad(target ? `sets:${target}` : null, () =>
    api.sets(target ?? ""),
  );
  const setList = (ready(sets) ?? []).filter((entry) => !isBenchmark(entry.set));
  // A benchmark's set is only ever opened for one of its tries; everywhere else, the app's own tests.
  const opensBenchmarkTry = place.view === "runs" && place.case !== null;
  const asked = place.set && (!isBenchmark(place.set) || opensBenchmarkTry) ? place.set : null;
  const set = asked ?? setList[0]?.set ?? null;
  const scope: Scope | null = target && set ? { target, set } : null;
  const scopeKey = scope ? `${scope.target}/${scope.set}` : null;

  const [cases, reloadCases] = useLoad(scopeKey ? `cases:${scopeKey}` : null, () =>
    api.cases(scope as Scope),
  );
  const [runsLoad, reloadRuns] = useLoad(scopeKey ? `runs:${scopeKey}` : null, () =>
    api.runs(scope as Scope),
  );
  // Each app's card, for its row in the picker.
  const names = targetList.map((entry) => entry.name).join("\n");
  const [cardsLoad] = useLoad(names ? `cards:${names}` : null, () =>
    Promise.all(
      names.split("\n").map((name) =>
        api.understanding(name).then(
          (understanding) => [name, understanding.card] as const,
          () => [name, null] as const,
        ),
      ),
    ).then((entries) => new Map<string, Card | null>(entries)),
  );
  const cards = ready(cardsLoad);
  const rows = ready(cases) ?? [];
  const runs = useMemo(
    () => [...(ready(runsLoad) ?? [])].sort((a, b) => b.started_at - a.started_at),
    [runsLoad],
  );
  // Benchmark runs stay out of `runs`, so they never count in the numbers of the app's own tests.
  const benchmarkSets = (ready(sets) ?? [])
    .map((entry) => entry.set)
    .filter(isBenchmark)
    .join("\n");
  const [benchmarkLoad, reloadBenchmarkRuns] = useLoad(
    target && benchmarkSets ? `benchmark-runs:${target}:${benchmarkSets}` : null,
    () =>
      Promise.all(
        benchmarkSets.split("\n").map((inSet) => api.runs({ target: target ?? "", set: inSet })),
      ),
  );
  const benchmarkRuns = useMemo(
    () => (ready(benchmarkLoad) ?? []).flat().sort((a, b) => b.started_at - a.started_at),
    [benchmarkLoad],
  );
  const goingBenchmark = benchmarkRuns.find(live) ?? null;
  useVisiblePoll(reloadBenchmarkRuns, POLL_MS, goingBenchmark !== null);
  const numbers = useMemo(() => runNumbers(runs), [runs]);
  const runLabel = (id: string) => `Run ${numbers.get(id) ?? id}`;
  const testName = (id: string) => rows.find((row) => row.case === id)?.name ?? id;
  const scopeLabel = (run: RunSummary) => {
    if (run.scope === "all") return "All tests";
    const ids = run.cases;
    if (ids.length === 1 && ids[0]) return testName(ids[0]);
    return count(ids.length || run.totals.cases, "test");
  };

  // The run going now, if any: followed until it settles, then the tests are read again.
  const going = runs.find(live) ?? null;
  // A run can start once the one going stopped playing, while its write-up goes on.
  const busy = runs.some(playing);
  useVisiblePoll(reloadRuns, POLL_MS, going !== null);
  const wasGoing = useRef(false);
  useEffect(() => {
    if (wasGoing.current && !going) void reloadCases();
    wasGoing.current = going !== null;
  }, [going, reloadCases]);

  return {
    targets,
    reloadTargets,
    targetList,
    target,
    sets,
    reloadSets,
    setList,
    set,
    scope,
    scopeKey,
    cases,
    reloadCases,
    runsLoad,
    reloadRuns,
    cards,
    rows,
    runs,
    runLabel,
    testName,
    scopeLabel,
    going,
    busy,
    benchmarkRuns,
    goingBenchmark,
    reloadBenchmarkRuns,
  };
}

export type Workspace = ReturnType<typeof useWorkspace>;

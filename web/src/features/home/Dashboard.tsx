import type { Health } from "../../lib/schema";
import { Areas } from "./Areas";
import { Attention } from "./Attention";
import { RecentRuns } from "./RecentRuns";
import { Tiles } from "./Tiles";
import { Trend } from "./Trend";

export function Dashboard({
  health,
  runLabel,
  onRun,
  onRuns,
  onTest,
  onReview,
}: {
  health: Health;
  runLabel: (run: string) => string;
  onRun: (run: string) => void;
  onRuns: () => void;
  onTest: (id: string, run?: string) => void;
  onReview: () => void;
}) {
  const act = (item: Health["attention"][number]) => {
    if (item.action === "review") onReview();
    else if (item.case)
      onTest(item.case, item.action === "see_why" ? (item.run ?? undefined) : undefined);
    else if (item.run) onRun(item.run);
  };
  return (
    <>
      <Tiles health={health} />

      <section className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,340px),1fr))] gap-[18px]">
        {health.areas.length ? <Areas areas={health.areas} /> : null}
        {health.trend.length > 1 ? <Trend trend={health.trend} /> : null}
      </section>

      <section className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,340px),1fr))] gap-[18px]">
        {health.attention.length ? <Attention items={health.attention} onAct={act} /> : null}

        {health.recent.length ? (
          <RecentRuns recent={health.recent} runLabel={runLabel} onRun={onRun} onRuns={onRuns} />
        ) : null}
      </section>
    </>
  );
}

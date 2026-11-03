import type { AgentSession } from "../../lib/schema";
import { LoadingState } from "../../ui/loading";

/** What the test writer is doing while it learns: its last step, the ones before under it. */
export function Learning({ activity }: { activity: AgentSession["activity"] }) {
  return (
    <div className="flex flex-col gap-1.5">
      <LoadingState label="Learning your app…" />
      {activity.length ? (
        <ol className="m-0 flex list-none flex-col gap-0.5 p-0 ps-[22px] text-[12px] text-ink-3">
          {activity
            .slice(-6)
            .reverse()
            .map((step) => (
              <li key={`${step.at}-${step.text}`}>{step.text}</li>
            ))}
        </ol>
      ) : null}
    </div>
  );
}

import type { AgentSession } from "../../lib/schema";
import { LoadingState } from "../../ui/loading";

/** The working line with what the writer did last, newest first under it. */
export function Working({ session }: { session: AgentSession }) {
  const { activity } = session;
  return (
    <div className="flex flex-col gap-1.5">
      <LoadingState label={activity.at(-1)?.text ?? "Working…"} />
      {activity.length > 1 ? (
        <ol className="m-0 flex list-none flex-col gap-0.5 p-0 ps-[22px] text-[12px] text-ink-3">
          {activity
            .slice(-6, -1)
            .reverse()
            .map((step) => (
              <li key={`${step.at}-${step.text}`}>{step.text}</li>
            ))}
        </ol>
      ) : null}
    </div>
  );
}

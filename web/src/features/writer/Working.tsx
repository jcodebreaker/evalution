import type { AgentSession } from "../../lib/schema";
import { LoadingState } from "../../ui/loading";

/** A step's shape without its numbers: "Summaries: 98/762" and "Summaries: 101/762" are one. */
const shape = (text: string) => text.replace(/\d+/g, "#");

/** The working line with what the writer did last, newest first under it. A step that only
 * counts up (a progress line) shows once, at its latest count, never as a stack of copies. */
export function Working({ session }: { session: AgentSession }) {
  const activity = session.activity.filter(
    (step, i, all) => i === all.length - 1 || shape(all[i + 1]?.text ?? "") !== shape(step.text),
  );
  return (
    <div className="flex flex-col gap-1.5">
      <LoadingState label={activity.at(-1)?.text ?? "Working…"} />
      {activity.length > 1 ? (
        <ol className="m-0 flex list-none flex-col gap-0.5 p-0 ps-[22px] text-[12.5px] text-ink-3">
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

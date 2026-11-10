import { useCallback, useState } from "react";
import { errorText, learnApi } from "../../lib/api";
import type { AreaState, LearnJob } from "../../lib/schema";
import { POLL_MS, ready, useLoad, useVisiblePoll } from "../../lib/use-load";

/** What "Go on" asks: what the stopped learn asked, with nothing forced. */
export const goOnBody = (job: LearnJob) => ({
  areas: job.areas_asked,
  everything: job.everything,
});

/** The app's learn, a background job the page follows: polled while it runs; `start` asks for one
 * (the facts, the areas named, or `everything`), `stop` stops it (what it learned is kept). */
export function useLearnJob(target: string | null) {
  const [load, reload] = useLoad(target ? `learn-job:${target}` : null, () =>
    learnApi.get(target ?? ""),
  );
  const [error, setError] = useState<string | null>(null);
  const job = ready(load);
  const running = job?.state === "running";
  useVisiblePoll(reload, POLL_MS, running);

  const start = useCallback(
    async (body: { areas?: readonly string[]; force?: boolean; everything?: boolean } = {}) => {
      if (!target) return;
      setError(null);
      try {
        await learnApi.start(target, body);
      } catch (failure) {
        setError(errorText(failure, "Couldn't start learning. Try again."));
      }
      await reload();
    },
    [target, reload],
  );

  const stop = useCallback(async () => {
    if (!target) return;
    await learnApi.stop(target).catch(() => null);
    await reload();
  }, [target, reload]);

  /** "Learn this area": the area as it stood (`current` spent nothing). */
  const learnArea = useCallback(
    async (area: string, force?: boolean): Promise<AreaState> => {
      if (!target) throw new Error("No app");
      const answer = await learnApi.learnArea(target, area, force);
      await reload();
      return answer.area;
    },
    [target, reload],
  );

  return { job, running, error, start, stop, learnArea, reload };
}

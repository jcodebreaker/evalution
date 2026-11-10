import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { appApi } from "../../lib/api";
import type { Isolation, IsolationView } from "../../lib/schema";
import { useVisiblePoll } from "../../lib/use-load";

/** How often the page reads the job again while it sets up and proves the test copy. */
export const ISOLATION_POLL_MS = 1_000;

/** The target's isolation plan, read and kept current while a job sets it up and proves it;
 * with what leaves the copy and how it is put back. `null`: nothing to read yet. */
export function useIsolation(target: string | null) {
  const [data, setData] = useState<IsolationView | null>(null);
  const [failed, setFailed] = useState(false);
  const read = useCallback(async () => {
    if (target === null) return;
    try {
      setData(await appApi.isolation(target));
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, [target]);
  useEffect(() => {
    setData(null);
    void read();
  }, [read]);
  useVisiblePoll(read, ISOLATION_POLL_MS, data?.job?.state === "running");
  /** An answer that carries the plan only keeps what leaves the copy as it was. */
  const set = useCallback((next: Isolation | IsolationView) => {
    setData((now) => ({
      outbound: now?.outbound ?? [],
      put_back: now?.put_back ?? null,
      ...next,
    }));
  }, []);
  return { data, failed, set, read };
}

export type IsolationState = ReturnType<typeof useIsolation>;

/** Opens the Test environment screen; `null` where the page can't move (a lone component). */
export const OpenEnvironment = createContext<(() => void) | null>(null);

export const useOpenEnvironment = () => useContext(OpenEnvironment);

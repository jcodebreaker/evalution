import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api } from "../../lib/api";
import type { Isolation } from "../../lib/schema";
import { useVisiblePoll } from "../../lib/use-load";

/** How often the page reads the job again while it sets up and proves the test copy. */
export const ISOLATION_POLL_MS = 1_000;

/** The target's isolation plan, read and kept current while a job sets it up and proves it. */
export function useIsolation(target: string) {
  const [data, setData] = useState<Isolation | null>(null);
  const [failed, setFailed] = useState(false);
  const read = useCallback(async () => {
    try {
      setData(await api.isolation(target));
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
  return { data, failed, set: setData };
}

/** Opens the Test environment screen; `null` where the page can't move (a lone component). */
export const OpenEnvironment = createContext<(() => void) | null>(null);

export const useOpenEnvironment = () => useContext(OpenEnvironment);

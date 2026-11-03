import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";

/** How often a screen reads again what the server is still working on. */
export const POLL_MS = 3_000;

export type Load<T> =
  | { status: "loading" }
  | { status: "missing" }
  | { status: "error" }
  | { status: "ready"; data: T };

/**
 * One server read keyed by `key`: `null` skips it. A reload keeps showing what is already there,
 * so polling never flashes a loading state.
 */
export function useLoad<T>(key: string | null, fetch: () => Promise<T>) {
  const [state, setState] = useState<Load<T>>({ status: "loading" });
  const latest = useRef(fetch);
  latest.current = fetch;
  const current = useRef(key);
  current.current = key;

  const reload = useCallback(async () => {
    const asked = current.current;
    if (asked === null) return;
    try {
      const data = await latest.current();
      if (current.current === asked) setState({ status: "ready", data });
    } catch (error) {
      if (current.current !== asked) return;
      const failed: Load<T> =
        error instanceof ApiError && error.status === 404
          ? { status: "missing" }
          : { status: "error" };
      setState((now) => (now.status === "ready" ? now : failed));
    }
  }, []);

  useEffect(() => {
    if (key === null) return;
    setState({ status: "loading" });
    void reload();
  }, [key, reload]);

  return [state, reload] as const;
}

export const ready = <T>(load: Load<T>): T | null => (load.status === "ready" ? load.data : null);

/** Calls `fn` every `ms` while `on` and the page is visible. */
export function useVisiblePoll(fn: () => unknown, ms: number, on: boolean) {
  const latest = useRef(fn);
  latest.current = fn;
  useEffect(() => {
    if (!on) return;
    const tick = () => {
      if (document.visibilityState === "visible") void latest.current();
    };
    const timer = window.setInterval(tick, ms);
    document.addEventListener("visibilitychange", tick);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", tick);
    };
  }, [ms, on]);
}

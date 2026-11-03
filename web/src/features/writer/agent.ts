import { useCallback, useEffect, useRef, useState } from "react";
import type { Scope } from "../../lib/api";
import { api } from "../../lib/api";
import type { AgentSession } from "../../lib/schema";
import { useVisiblePoll } from "../../lib/use-load";

const WORKING_POLL_MS = 2_000;

/** Raise adds the default budget again. */
export const RAISE_USD = 10;

/** One thread per app. */
const storageKey = ({ target }: Scope) => `evalution.writer.${target}`;

function stored(scope: Scope): string | null {
  try {
    return localStorage.getItem(storageKey(scope));
  } catch {
    return null;
  }
}

function store(scope: Scope, id: string | null) {
  try {
    if (id) localStorage.setItem(storageKey(scope), id);
    else localStorage.removeItem(storageKey(scope));
  } catch {
    // Without storage the session is only kept for this visit.
  }
}

/**
 * The app's test writer thread: the open one is remembered per app, followed while the writer
 * works, and opened on the first ask. A message written while the agent works waits here
 * and is sent as soon as it stops.
 */
export function useAgent(scope: Scope | null) {
  const [session, setSession] = useState<AgentSession | null>(null);
  const [queued, setQueued] = useState<{ text: string; run?: string }[]>([]);
  const key = scope?.target ?? null;
  const current = useRef(key);
  current.current = key;
  const scopeRef = useRef(scope);
  scopeRef.current = scope;

  useEffect(() => {
    setSession(null);
    setQueued([]);
    const asked = scopeRef.current;
    const id = asked ? stored(asked) : null;
    if (!asked || !id) return;
    api.agent.session(id).then(
      (found) => {
        if (current.current === key) setSession(found);
      },
      () => store(asked, null),
    );
  }, [key]);

  const refresh = useCallback(async () => {
    if (!session) return;
    const found = await api.agent.session(session.id);
    if (current.current === key) setSession(found);
  }, [session, key]);
  const working = session?.status === "working";
  useVisiblePoll(refresh, WORKING_POLL_MS, working);

  const sendNow = useCallback(
    async (text: string, run?: string) => {
      const asked = scopeRef.current;
      if (!asked) return;
      const open = session && session.status !== "stopped" ? session : await api.agent.open(asked);
      store(asked, open.id);
      const next = await api.agent.send(open.id, text, run);
      if (current.current === key) setSession(next);
    },
    [session, key],
  );

  /** Sends `text`, or queues it while the agent works. `run` says which run it is about, for the
   * agent; it isn't part of what the person said. */
  const ask = useCallback(
    async (text: string, run?: string) => {
      if (working) setQueued((now) => [...now, { text, run }]);
      else await sendNow(text, run);
    },
    [working, sendNow],
  );

  // The agent stopped working: send what waited, one message at a time.
  const sending = useRef(false);
  useEffect(() => {
    const next = queued[0];
    if (working || next === undefined || sending.current) return;
    sending.current = true;
    sendNow(next.text, next.run).finally(() => {
      sending.current = false;
      setQueued((now) => now.slice(1));
    });
  }, [working, queued, sendNow]);

  /** The admin approves a plan: the agent adds its test. */
  const approve = useCallback(
    async (planId: string) => {
      if (!session) return;
      const next = await api.agent.approve(session.id, planId);
      if (current.current === key) setSession(next);
    },
    [session, key],
  );

  /** A session the server opened on the page's behalf (a plan asked for elsewhere) becomes the open one. */
  const adopt = useCallback(
    (next: AgentSession) => {
      const asked = scopeRef.current;
      if (!asked || current.current !== key) return;
      store(asked, next.id);
      setSession(next);
    },
    [key],
  );

  const raise = useCallback(async () => {
    if (!session) return;
    setSession(await api.agent.budget(session.id, session.budget_usd + RAISE_USD));
  }, [session]);
  const stop = useCallback(async () => {
    if (!session) return;
    setQueued([]);
    setSession(await api.agent.stop(session.id));
  }, [session]);

  return { session, queued, ask, approve, adopt, refresh, raise, stop };
}

export type Agent = ReturnType<typeof useAgent>;

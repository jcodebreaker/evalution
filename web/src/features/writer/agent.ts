import { useCallback, useEffect, useRef, useState } from "react";
import type { Scope } from "../../lib/api";
import { api } from "../../lib/api";
import type { AgentSession } from "../../lib/schema";
import { useVisiblePoll } from "../../lib/use-load";

const WORKING_POLL_MS = 2_000;

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

type Waiting = { text: string; run?: string; learn?: boolean };
const NONE: readonly Waiting[] = [];

/**
 * The app's test writer thread: the open one is remembered per app, followed while the writer
 * works, and opened on the first ask. A message written while the agent works waits here, kept
 * per app like the draft, and is sent as soon as that app's agent stops.
 */
export function useAgent(scope: Scope | null) {
  const [session, setSession] = useState<AgentSession | null>(null);
  const [queues, setQueues] = useState<Record<string, readonly Waiting[]>>({});
  const key = scope?.target ?? null;
  const queued = (key && queues[key]) || NONE;
  /** The app whose thread is read: until it is, what waits for that app isn't sent. */
  const [readFor, setReadFor] = useState<string | null>(null);
  const current = useRef(key);
  current.current = key;
  const scopeRef = useRef(scope);
  scopeRef.current = scope;

  const setQueued = useCallback(
    (app: string | null, change: (now: readonly Waiting[]) => readonly Waiting[]) => {
      if (app) setQueues((all) => ({ ...all, [app]: change(all[app] ?? NONE) }));
    },
    [],
  );

  useEffect(() => {
    setSession(null);
    setReadFor(null);
    const asked = scopeRef.current;
    const id = asked ? stored(asked) : null;
    if (!asked || !id) {
      setReadFor(key);
      return;
    }
    api.agent.session(id).then(
      (found) => {
        if (current.current !== key) return;
        setSession(found);
        setReadFor(key);
      },
      () => {
        store(asked, null);
        if (current.current === key) setReadFor(key);
      },
    );
  }, [key]);

  /** Leaves this thread for an empty one; it stays stored on the server. */
  const newChat = useCallback(() => {
    const asked = scopeRef.current;
    if (asked) store(asked, null);
    setSession(null);
    setQueued(current.current, () => NONE);
  }, [setQueued]);

  /** Back to an earlier thread of this app. */
  const openChat = useCallback(
    async (id: string) => {
      const asked = scopeRef.current;
      const found = await api.agent.session(id);
      if (asked) store(asked, id);
      setQueued(asked?.target ?? null, () => NONE);
      setSession(found);
    },
    [setQueued],
  );

  const refresh = useCallback(async () => {
    if (!session) return;
    const found = await api.agent.session(session.id);
    if (current.current === key) setSession(found);
  }, [session, key]);
  const working = session?.status === "working";
  useVisiblePoll(refresh, WORKING_POLL_MS, working);

  const sendNow = useCallback(
    async (text: string, run?: string, learn?: boolean) => {
      const asked = scopeRef.current;
      if (!asked) return;
      const open = session && session.status !== "stopped" ? session : await api.agent.open(asked);
      store(asked, open.id);
      const next = await api.agent.send(open.id, text, run, learn);
      if (current.current === key) setSession(next);
    },
    [session, key],
  );

  /** Sends `text`, or queues it while the agent works. `run` says which run it is about, for the
   * agent; it isn't part of what the person said. `learn`: it asks to learn the app. */
  const ask = useCallback(
    async (text: string, run?: string, learn?: boolean) => {
      if (working) setQueued(key, (now) => [...now, { text, run, learn }]);
      else await sendNow(text, run, learn);
    },
    [working, sendNow, key, setQueued],
  );

  // This app's agent stopped working, its thread read: send what waited, one message at a time.
  const [sending, setSending] = useState(false);
  useEffect(() => {
    const next = queued[0];
    if (working || next === undefined || sending || readFor !== key) return;
    setSending(true);
    sendNow(next.text, next.run, next.learn).finally(() => {
      setSending(false);
      setQueued(key, (now) => now.slice(1));
    });
  }, [working, queued, sending, readFor, key, sendNow, setQueued]);

  /** The admin takes the suggestion on a flagged rubric line of a plan, before approving it. */
  const swapLine = useCallback(
    async (planId: string, line: string) => {
      if (!session) return;
      const next = await api.agent.swapLine(session.id, planId, line);
      if (current.current === key) setSession(next);
    },
    [session, key],
  );

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

  const stop = useCallback(async () => {
    if (!session) return;
    setQueued(key, () => NONE);
    setSession(await api.agent.stop(session.id));
  }, [session, key, setQueued]);

  return { session, queued, ask, approve, swapLine, adopt, refresh, stop, newChat, openChat };
}

export type Agent = ReturnType<typeof useAgent>;

import { useCallback, useEffect, useState } from "react";
import type { Scope } from "../../lib/api";
import { useAgent } from "../writer/agent";

/** The test writer as the page uses it: its thread, its panel, and learning a new app. */
export function useWriter(
  scope: Scope | null,
  reloadRuns: () => Promise<unknown>,
  reloadCases: () => Promise<unknown>,
) {
  const [writerOpen, setWriterOpen] = useState(false);
  const [writerDraft, setWriterDraft] = useState("");
  const clearDraft = useCallback(() => setWriterDraft(""), []);

  const agent = useAgent(scope);
  // What the test writer adds and starts shows up in the tests and runs as it goes.
  const agentMessages = agent.session?.messages.length ?? 0;
  useEffect(() => {
    if (agentMessages) {
      void reloadRuns();
      void reloadCases();
    }
  }, [agentMessages, reloadRuns, reloadCases]);
  /** Hands `text` to the test writer and opens its panel. */
  const ask = async (text: string, run?: string, learn?: boolean) => {
    await agent.ask(text, run, learn);
    setWriterOpen(true);
  };
  // An app just connected: once its test set is open, the test writer learns it.
  const [learn, setLearn] = useState<string | null>(null);
  const { ask: askAgent, session: agentSession } = agent;
  useEffect(() => {
    if (!learn || scope?.target !== learn) return;
    if (agentSession && agentSession.target !== learn) return;
    setLearn(null);
    void askAgent(
      `Learn ${learn}: build its profile from its recent traces and its code.`,
      undefined,
      true,
    );
  }, [learn, scope?.target, agentSession, askAgent]);

  return {
    agent,
    agentMessages,
    ask,
    setLearn,
    writerOpen,
    setWriterOpen,
    writerDraft,
    setWriterDraft,
    clearDraft,
  };
}

export type Writer = ReturnType<typeof useWriter>;

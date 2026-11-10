import { useCallback, useEffect, useState } from "react";
import { learnApi, type Scope } from "../../lib/api";
import { useAgent } from "../writer/agent";

/** The test writer as the page uses it: its thread, its panel, and learning a new app. */
export function useWriter(
  scope: Scope | null,
  reloadRuns: () => Promise<unknown>,
  reloadCases: () => Promise<unknown>,
) {
  const [writerOpen, setOpen] = useState(false);
  /**
   * Why the panel is open: `screen`, from the assistant's button, to ask about what is on screen (its
   * chip in the composer); `write`, from a screen's own button (New test, Write a test, Ask), whose
   * message carries what it is about.
   */
  const [mode, setMode] = useState<"screen" | "write">("write");
  /** Opens (to write a test) or closes the panel. */
  const setWriterOpen = useCallback((open: boolean) => {
    if (open) setMode("write");
    setOpen(open);
  }, []);
  /** Opens the panel from the assistant's button, about what is on screen. */
  const openAssistant = useCallback(() => {
    setMode("screen");
    setOpen(true);
  }, []);
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
  // An app just connected: its learn job starts (the facts first, in seconds), in the
  // background; the setup screen and What it learned follow it, not the chat.
  const [learn, setLearn] = useState<string | null>(null);
  useEffect(() => {
    if (!learn) return;
    setLearn(null);
    void learnApi.start(learn).catch(() => undefined);
  }, [learn]);

  return {
    agent,
    agentMessages,
    ask,
    setLearn,
    writerOpen,
    setWriterOpen,
    mode,
    openAssistant,
    writerDraft,
    setWriterDraft,
    clearDraft,
  };
}

export type Writer = ReturnType<typeof useWriter>;

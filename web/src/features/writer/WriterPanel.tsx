import { ArrowUp, Square, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { cn } from "../../lib/cn";
import { dollars, usd } from "../../lib/format";
import type { RunSummary } from "../../lib/schema";
import { Button } from "../../ui/button";
import { Group } from "../shared/group";
import type { Agent } from "./agent";
import { RAISE_USD } from "./agent";
import { onEnter, TEXTAREA, useAsk } from "./ask";
import { Bubble } from "./Bubble";
import { Working } from "./Working";
import { WriterReply } from "./WriterReply";

/**
 * The test writer, from the end edge of every screen: the thread with what it read, its plans,
 * the tests it added and the runs it reviewed; the reply field at the bottom also takes a message
 * while it works.
 */
export function WriterPanel({
  agent,
  runs,
  runLabel,
  testName,
  going,
  onClose,
  onRun,
  onRunTest,
  onTest,
  onLearned,
  draft,
  onDrafted,
}: {
  agent: Agent;
  runs: readonly RunSummary[];
  runLabel: (run: string) => string;
  testName: (id: string) => string;
  /** A run is going: Run it waits for it. */
  going: boolean;
  onClose: () => void;
  onRun: (run: string) => void;
  onRunTest: (id: string) => void;
  onTest: (id: string, run?: string) => void;
  onLearned: () => void;
  /** Words put in the reply field for the owner to send. */
  draft: string;
  onDrafted: () => void;
}) {
  const { session, queued } = agent;
  const [text, setText] = useState("");
  useEffect(() => {
    if (!draft) return;
    setText(draft);
    onDrafted();
  }, [draft, onDrafted]);
  const { busy, error, send } = useAsk(agent.ask);
  const approval = useAsk(agent.approve);
  const end = useRef<HTMLDivElement>(null);
  const working = session?.status === "working";
  const total = (session?.messages.length ?? 0) + queued.length;

  // A new message, or the writer starting to work, brings the end of the thread into view.
  useEffect(() => {
    if (total || working) end.current?.scrollIntoView({ block: "end" });
  }, [total, working]);

  const submit = () => {
    void send(text).then((sent) => {
      if (sent) setText("");
    });
  };

  const lastAgent = session
    ? [...session.messages].reverse().find((message) => message.role === "agent")
    : undefined;
  const canAct = !working && !busy && !approval.busy;
  const next = session ? dollars(session.budget_usd + RAISE_USD) : "";

  return (
    <aside
      aria-label="Test writer"
      className="flex min-h-0 flex-col border-s border-separator bg-content max-md:fixed max-md:inset-0 max-md:z-40 max-md:border-s-0"
    >
      <header className="flex min-h-[52px] items-center gap-2 border-b border-separator px-4 py-2">
        <div className="flex min-w-0 flex-1 flex-col">
          <h2 className="m-0 truncate text-[15px] font-semibold tracking-[-0.2px]">Test writer</h2>
          {session ? (
            <span className="truncate text-[12px] text-ink-3 tabular-nums">
              {usd(session.spent_usd)} of {dollars(session.budget_usd)}
            </span>
          ) : null}
        </div>
        {working ? (
          <Button size="sm" variant="secondary" onClick={() => void agent.stop()}>
            <Square />
            Stop
          </Button>
        ) : null}
        <Button variant="ghost" size="icon-sm" aria-label="Close" onClick={onClose}>
          <X />
        </Button>
      </header>

      <div className="min-h-0 flex-1 overflow-auto">
        <div className="flex flex-col gap-3.5 px-4 pt-4 pb-4">
          {session?.messages.map((message) =>
            message.role === "admin" ? (
              <Bubble key={message.id} text={message.text} />
            ) : (
              <WriterReply
                key={message.id}
                message={message}
                latest={message === lastAgent && canAct}
                runs={runs}
                runLabel={runLabel}
                testName={testName}
                going={going}
                onReply={(words) => void send(words)}
                onApprove={(planId) => void approval.send(planId)}
                onRun={onRun}
                onRunTest={onRunTest}
                onTest={onTest}
                onLearned={onLearned}
              />
            ),
          )}
          {working && session ? <Working session={session} /> : null}
          {queued.map((entry, index) => (
            <Bubble key={`queued-${index}`} text={entry.text} waiting />
          ))}
          {session?.status === "waiting_budget" ? (
            <Group className="flex flex-wrap items-center gap-2.5 px-3.5 py-3 text-[13px]">
              <span className="min-w-0 flex-1 text-ink-2">
                Over the {dollars(session.budget_usd)} budget
              </span>
              <Button size="sm" onClick={() => void agent.raise()}>
                Raise to {next}
              </Button>
            </Group>
          ) : null}
          <div ref={end} />
        </div>
      </div>

      <div className="border-t border-separator px-4 py-3">
        <div className="flex items-end gap-2">
          <textarea
            // biome-ignore lint/a11y/noAutofocus: the panel opens to be written in
            autoFocus={!session}
            value={text}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={onEnter(submit)}
            placeholder={session ? "Reply" : "What should we test?"}
            aria-label={session ? "Reply to the test writer" : "What should we test?"}
            className={cn(TEXTAREA, "min-h-6 text-[14px]")}
          />
          <Button size="icon-sm" aria-label="Send" disabled={busy || !text.trim()} onClick={submit}>
            <ArrowUp />
          </Button>
        </div>
        {error || approval.error ? (
          <p className="m-0 pt-2 text-[12px] text-destructive">{error ?? approval.error}</p>
        ) : null}
      </div>
    </aside>
  );
}

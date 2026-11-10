import { ArrowUp, History, Square, SquarePen, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../../lib/api";
import { cn } from "../../lib/cn";
import { ago } from "../../lib/format";
import type { RunSummary } from "../../lib/schema";
import { useLoad } from "../../lib/use-load";
import { Button } from "../../ui/button";
import { Logo } from "../shell/Logo";
import type { Agent } from "./agent";
import { onEnter, TEXTAREA, useAsk } from "./ask";
import { Bubble } from "./Bubble";
import type { ScreenContext } from "./context";
import { askedText, withContext } from "./context";
import { Working } from "./Working";
import { WriterReply } from "./WriterReply";

/**
 * The test writer, from the end edge of every screen: the thread with what it read, its plans,
 * the tests it added and the runs it reviewed; the reply field at the bottom also takes a message
 * while it works.
 */
const STARTERS = [
  "Why did my last run fail?",
  "Which test should I write next?",
  "Where are the tokens going?",
];

/** The app's earlier chats, latest first. */
function PastChats({
  target,
  current,
  onPick,
}: {
  target: string;
  current: string | null;
  onPick: (id: string) => void;
}) {
  const [chats] = useLoad(`chats:${target}`, () => api.agent.chats(target));
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-1 overflow-auto p-2">
      {chats.status === "ready" && !chats.data.length ? (
        <p className="m-0 p-2 text-[14px] text-ink-2">No past chats yet.</p>
      ) : null}
      {chats.status === "ready"
        ? chats.data.map((chat) => (
            <button
              key={chat.id}
              type="button"
              aria-current={chat.id === current ? "true" : undefined}
              onClick={() => onPick(chat.id)}
              className={cn(
                "flex min-w-0 flex-col gap-0.5 rounded-[7px] px-2.5 py-2 text-start hover:bg-sunk focus-visible:outline-2 focus-visible:outline-tint",
                chat.id === current && "bg-tint-soft",
              )}
            >
              <span className="truncate text-[14px]">{askedText(chat.asked).asked}</span>
              <span className="text-[11.5px] text-ink-3">{ago(chat.at)}</span>
            </button>
          ))
        : null}
    </div>
  );
}

export function WriterPanel({
  target,
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
  context,
}: {
  target: string | null;
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
  /**
   * What is on screen, when the panel was opened to ask about it: a chip in the composer, sent with
   * each question (before it, in plain words, and its run by the API) until the person removes it.
   * Null when it was opened to write a test.
   */
  context: ScreenContext | null;
}) {
  const { session, queued } = agent;
  const [past, setPast] = useState(false);
  const [text, setText] = useState("");
  useEffect(() => {
    if (!draft) return;
    setText(draft);
    onDrafted();
  }, [draft, onDrafted]);
  // The chip removed, for this screen: a new screen brings its own.
  const [dropped, setDropped] = useState<string | null>(null);
  const attached = context && context.key !== dropped ? context : null;
  const { busy, error, send } = useAsk((words) =>
    agent.ask(withContext(words, attached), attached?.run ?? undefined),
  );
  // A reply to the writer's own question (a plan's) carries no screen.
  const reply = useAsk(agent.ask);
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
  // "Use this" acts only on the latest plan, the one still waiting for approval.
  const swap = useAsk((line) => agent.swapLine(lastAgent?.plan?.id ?? "", line));
  const canAct = !working && !busy && !reply.busy && !approval.busy && !swap.busy;

  return (
    <aside
      aria-label="Ask AI"
      className="flex min-h-0 flex-col border-s border-separator bg-group max-md:fixed max-md:inset-0 max-md:z-40 max-md:border-s-0"
    >
      <header className="flex h-[52px] shrink-0 items-center gap-1 border-b border-separator ps-4 pe-2">
        <Logo className="me-1.5 size-6 shrink-0" />
        <h2 className="m-0 flex-1 truncate text-[14px] font-semibold">Ask AI</h2>
        {working ? (
          <Button size="sm" variant="secondary" onClick={() => void agent.stop()}>
            <Square />
            Stop
          </Button>
        ) : null}
        {working ? null : (
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Past chats"
            aria-expanded={past}
            title="Past chats"
            onClick={() => setPast((now) => !now)}
          >
            <History />
          </Button>
        )}
        {session && !working ? (
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="New chat"
            title="New chat"
            onClick={() => {
              setPast(false);
              agent.newChat();
            }}
          >
            <SquarePen />
          </Button>
        ) : null}
        <Button variant="ghost" size="icon-sm" aria-label="Close" onClick={onClose}>
          <X />
        </Button>
      </header>

      {past && target ? (
        <PastChats
          target={target}
          current={session?.id ?? null}
          onPick={(id) => {
            setPast(false);
            void agent.openChat(id);
          }}
        />
      ) : null}
      <div className={cn("min-h-0 flex-1 overflow-auto", past && target && "hidden")}>
        <div className="flex flex-col gap-4 px-4 pt-4 pb-4">
          {session ? null : (
            <div className="flex flex-col gap-2 pt-1">
              <p className="m-0 text-[14px] font-medium">What do you want to know?</p>
              {STARTERS.map((starter) => (
                <button
                  key={starter}
                  type="button"
                  onClick={() => void send(starter)}
                  className="rounded-[8px] border border-separator bg-group px-3 py-2 text-start text-[14px] text-ink-2 hover:bg-sunk hover:text-ink focus-visible:outline-2 focus-visible:outline-tint"
                >
                  {starter}
                </button>
              ))}
            </div>
          )}
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
                onReply={(words) => void reply.send(words)}
                onApprove={(planId) => void approval.send(planId)}
                onSwapLine={(line) => void swap.send(line)}
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
          <div ref={end} />
        </div>
      </div>

      <div className="px-3 pt-1 pb-3">
        <div className="flex flex-col gap-2 rounded-[10px] border border-rule-strong bg-group px-3 py-2 focus-within:border-tint focus-within:ring-3 focus-within:ring-tint-soft-2">
          {attached ? (
            <span className="inline-flex h-[28px] max-w-full items-center gap-1.5 self-start rounded-[7px] bg-tint-soft ps-2.5 pe-1 text-[13px] text-tint-ink">
              <span className="min-w-0 truncate" title={attached.label}>
                {attached.label}
              </span>
              <button
                type="button"
                aria-label="Remove context"
                title="Ask without it"
                onClick={() => setDropped(attached.key)}
                className="grid size-5 shrink-0 place-items-center rounded-full hover:bg-tint-soft-2 focus-visible:outline-2 focus-visible:outline-tint"
              >
                <X className="size-3.5" aria-hidden />
              </button>
            </span>
          ) : null}
          <div className="flex items-end gap-2">
            <textarea
              // biome-ignore lint/a11y/noAutofocus: the panel opens to be written in
              autoFocus={!session}
              value={text}
              onChange={(event) => setText(event.target.value)}
              onKeyDown={onEnter(submit)}
              placeholder={
                attached ? "Ask about what's on screen" : "Ask about your runs, traces or tests"
              }
              aria-label="Ask about your runs, traces or tests"
              className={cn(TEXTAREA, "min-h-6 py-0.5 text-[13.5px]")}
            />
            <Button
              size="icon-sm"
              aria-label="Send"
              disabled={busy || !text.trim()}
              onClick={submit}
            >
              <ArrowUp />
            </Button>
          </div>
        </div>
        {error || reply.error || approval.error || swap.error ? (
          <p className="m-0 pt-2 text-[12.5px] text-fail">
            {error ?? reply.error ?? approval.error ?? swap.error}
          </p>
        ) : null}
      </div>
    </aside>
  );
}

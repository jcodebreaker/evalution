import { CornerDownLeft, MessageSquare } from "lucide-react";
import { useState } from "react";
import { api } from "../../lib/api";
import { cn } from "../../lib/cn";
import type { LibraryRow, Source } from "../../lib/schema";
import { useLoad } from "../../lib/use-load";
import { Spinner } from "../../ui/loading";
import { onEnter, TEXTAREA, useAsk } from "../writer/ask";
import { Conversations } from "./Conversations";

/** A real conversation as the ask a test is written from. */
function fromConversation(source: Source): string {
  const round = source.round_id ? `, round ${source.round_id}` : "";
  return `Write a test from this real conversation (session ${source.session_id}${round}): "${source.ask}"`;
}

/** The ask for a test of a kind, in the owner's own words. */
function fromKind(kind: LibraryRow, words: Record<string, string>): string {
  const lines = kind.asks.map((ask) => `${ask.label}: ${words[ask.name]?.trim()}`);
  return [`Write a test from the library test ${kind.id}.`, ...lines].join("\n");
}

/** What should we test, in plain words or from a real conversation, handed to the test writer. */
export function Composer({
  target,
  onAsk,
}: {
  target: string;
  onAsk: (text: string) => Promise<void>;
}) {
  const [text, setText] = useState("");
  const [picking, setPicking] = useState(false);
  const [kind, setKind] = useState<LibraryRow | null>(null);
  const [words, setWords] = useState<Record<string, string>>({});
  const { busy, error, send } = useAsk(onAsk);
  const [library] = useLoad(`library:${target}`, () => api.library(target));
  const kinds =
    library.status === "ready" ? library.data.filter((entry) => entry.kind !== null) : [];
  const filled = kind?.asks.every((ask) => words[ask.name]?.trim()) ?? false;
  const submit = () => {
    if (kind) {
      if (!filled) return;
      void send(fromKind(kind, words)).then((sent) => {
        if (sent) {
          setKind(null);
          setWords({});
        }
      });
      return;
    }
    void send(text).then((sent) => {
      if (sent) setText("");
    });
  };
  const label = "font-mono text-[10.5px] tracking-[0.08em] text-deep-fg-2 uppercase";
  const disabled = busy || (kind ? !filled : !text.trim());
  return (
    <section className="overflow-hidden rounded-xl border border-deep-line bg-deep text-deep-fg">
      <div className="flex flex-col gap-3.5 p-5">
        <span className={label}>Write a test</span>
        {kind ? (
          <div className="flex flex-col gap-2">
            <span className="flex items-center gap-3 text-[15px] font-medium">
              {kind.title}
              <button
                type="button"
                className="text-[12px] font-normal text-deep-fg-2 underline underline-offset-4 hover:text-deep-fg"
                onClick={() => setKind(null)}
              >
                Back
              </button>
            </span>
            {kind.asks.map((ask) => (
              <label key={ask.name} className="flex flex-col gap-1 text-[12.5px] text-deep-fg-2">
                {ask.label}
                <input
                  value={words[ask.name] ?? ""}
                  onChange={(event) =>
                    setWords((now) => ({ ...now, [ask.name]: event.target.value }))
                  }
                  className="rounded-md border border-deep-line bg-transparent px-2.5 py-1.5 text-[13.5px] text-deep-fg outline-none focus:border-deep-fg-2"
                />
              </label>
            ))}
          </div>
        ) : null}
        <div className="flex items-end gap-2 rounded-lg border border-deep-line p-2 focus-within:border-deep-fg-2">
          {kind ? (
            <span className="min-w-0 flex-1 px-1.5 py-1 text-[13px] text-deep-fg-2">
              Ready when every field is filled
            </span>
          ) : (
            <textarea
              value={text}
              onChange={(event) => setText(event.target.value)}
              onKeyDown={onEnter(submit)}
              placeholder="What should we test?"
              aria-label="What should we test?"
              className={cn(
                TEXTAREA,
                "px-1.5 py-1 text-[14.5px] text-deep-fg placeholder:text-deep-fg-2",
              )}
            />
          )}
          <button
            type="button"
            aria-label="Write the test"
            title="Write the test"
            disabled={disabled}
            onClick={submit}
            className="grid size-9 shrink-0 place-items-center rounded-md bg-deep-accent text-white hover:brightness-110 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-deep-fg disabled:opacity-40"
          >
            {busy ? (
              <Spinner className="border-white border-r-transparent" />
            ) : (
              <CornerDownLeft className="size-4" aria-hidden />
            )}
          </button>
        </div>
        {error ? <p className="m-0 text-[12px] text-red-300">{error}</p> : null}
        <div className="flex flex-col gap-2">
          <span className={label}>Or start from</span>
          <div className="flex flex-wrap gap-1.5">
            {kinds.map((entry) => {
              const fits = entry.fit.verdict === "fits";
              return (
                <button
                  key={entry.id}
                  type="button"
                  disabled={!fits}
                  aria-pressed={kind?.id === entry.id}
                  title={fits ? entry.checks : entry.fit.label}
                  onClick={() => setKind(entry)}
                  className={cn(
                    "flex items-center gap-2 rounded-md border border-deep-line px-2.5 py-1.5 text-[12.5px] hover:border-deep-fg-2 focus-visible:outline-2 focus-visible:outline-deep-fg disabled:opacity-55 disabled:hover:border-deep-line",
                    kind?.id === entry.id && "border-deep-fg-2 bg-deep-accent/25",
                  )}
                >
                  {entry.title}
                  <span
                    className={cn(
                      "flex items-center gap-1 font-mono text-[9.5px] tracking-[0.06em] uppercase",
                      fits ? "text-chart-5" : "text-deep-fg-2",
                    )}
                  >
                    <span
                      className={cn("size-1.5 rounded-full", fits ? "bg-chart-2" : "bg-deep-fg-2")}
                    />
                    {fits ? "Ready" : "Can't run here"}
                  </span>
                </button>
              );
            })}
            <button
              type="button"
              aria-expanded={picking}
              onClick={() => setPicking((now) => !now)}
              className="flex items-center gap-2 rounded-md border border-deep-line px-2.5 py-1.5 text-[12.5px] hover:border-deep-fg-2 focus-visible:outline-2 focus-visible:outline-deep-fg"
            >
              <MessageSquare className="size-3.5" aria-hidden />A real conversation
            </button>
          </div>
        </div>
      </div>
      {picking ? (
        <div className="bg-group text-foreground">
          <Conversations
            target={target}
            onPick={(source) => {
              void send(fromConversation(source)).then((sent) => {
                if (sent) setPicking(false);
              });
            }}
          />
        </div>
      ) : null}
    </section>
  );
}

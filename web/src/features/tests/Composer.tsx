import { LayoutTemplate, MessageSquare, Sparkles } from "lucide-react";
import { useState } from "react";
import { api } from "../../lib/api";
import type { LibraryRow, Source } from "../../lib/schema";
import { useLoad } from "../../lib/use-load";
import { Button } from "../../ui/button";
import { Input } from "../../ui/field";
import { Spinner } from "../../ui/loading";
import { Group } from "../shared/group";
import { Row } from "../shared/Row";
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
  const [choosing, setChoosing] = useState(false);
  const [kind, setKind] = useState<LibraryRow | null>(null);
  const [words, setWords] = useState<Record<string, string>>({});
  const { busy, error, send } = useAsk(onAsk);
  const [library] = useLoad(choosing ? `library:${target}` : null, () => api.library(target));
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
          setChoosing(false);
        }
      });
      return;
    }
    void send(text).then((sent) => {
      if (sent) setText("");
    });
  };
  return (
    <Group className="flex flex-col">
      <div className="flex flex-col gap-2.5 px-3.5 pt-3.5 pb-2.5">
        {kind ? null : (
          <textarea
            value={text}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={onEnter(submit)}
            placeholder="What should we test?"
            aria-label="What should we test?"
            className={TEXTAREA}
          />
        )}
        {kind ? (
          <div className="flex flex-col">
            <span className="pb-1 text-[14px] font-medium">{kind.title}</span>
            {kind.asks.map((ask) => (
              <Row key={ask.name} label={ask.label}>
                <Input
                  value={words[ask.name] ?? ""}
                  onChange={(event) =>
                    setWords((now) => ({ ...now, [ask.name]: event.target.value }))
                  }
                />
              </Row>
            ))}
          </div>
        ) : null}
        {choosing && !kind ? (
          <div className="flex flex-wrap gap-1.5">
            {kinds.map((entry) => (
              <Button
                key={entry.id}
                variant="secondary"
                size="xs"
                disabled={entry.fit.verdict !== "fits"}
                title={entry.fit.verdict === "fits" ? entry.checks : entry.fit.label}
                onClick={() => setKind(entry)}
              >
                {entry.title}
              </Button>
            ))}
          </div>
        ) : null}
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            aria-expanded={choosing}
            className="-ms-1.5 text-ink-2"
            onClick={() => {
              setChoosing((now) => !now);
              setKind(null);
            }}
          >
            <LayoutTemplate />
            From a kind of test
          </Button>
          <Button
            variant="ghost"
            size="sm"
            aria-expanded={picking}
            className="-ms-1.5 text-ink-2"
            onClick={() => setPicking((now) => !now)}
          >
            <MessageSquare />
            From a real conversation
          </Button>
          {error ? (
            <p className="m-0 min-w-0 flex-1 text-[12.5px] text-destructive">{error}</p>
          ) : null}
          <Button
            className="ms-auto"
            disabled={busy || (kind ? !filled : !text.trim())}
            onClick={submit}
          >
            {busy ? (
              <Spinner className="border-primary-foreground border-r-transparent" />
            ) : (
              <Sparkles />
            )}
            Write the test
          </Button>
        </div>
      </div>
      {picking ? (
        <Conversations
          target={target}
          onPick={(source) => {
            void send(fromConversation(source)).then((sent) => {
              if (sent) setPicking(false);
            });
          }}
        />
      ) : null}
    </Group>
  );
}

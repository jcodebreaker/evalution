import { ArrowUp } from "lucide-react";
import type { FormEvent, ReactNode } from "react";
import { useEffect, useRef, useState } from "react";
import { cn } from "../../lib/cn";
import type { Health } from "../../lib/schema";
import {
  headline,
  ITS_READING,
  MODEL_ONLY,
  NOT_VERIFIED,
  SUGGESTION,
  unjudgedWords,
  verdictWord,
} from "../../lib/verdict";
import { Button } from "../../ui/button";
import { TextLink } from "../shared/group";
import { CARD } from "./card";

/** The latest run in plain words, and a place to ask about it. */
export function ReportCard({
  report,
  runLabel,
  onAsk,
  onTest,
}: {
  report: NonNullable<Health["report"]>;
  runLabel: (run: string) => string;
  onAsk: (text: string, run: string) => void;
  onTest: (id: string, run?: string) => void;
}) {
  const [text, setText] = useState("");
  const label = runLabel(report.run);
  const facts = report.facts;
  const send = (event: FormEvent) => {
    event.preventDefault();
    const asked = text.trim();
    if (!asked) return;
    onAsk(`About ${label}: ${asked}`, report.run);
    setText("");
  };
  return (
    <section className={cn(CARD, "flex flex-col gap-4")}>
      <h2 className="m-0 text-[20px] font-semibold tracking-[-0.015em] text-balance">
        {headline(facts)}
      </h2>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,240px),1fr))] gap-6">
        <ReportList title="Passed before, fails now">
          {facts.regressions.map((item) => (
            <Item key={item.case} text={item.title} onWhy={() => onTest(item.case, report.run)} />
          ))}
        </ReportList>
        <ReportList title="Went well">
          {[
            ...facts.held.map((item) => (
              <Item
                key={item.case}
                text={`${item.name}: ${item.passed} of ${item.judged} tries`}
                notes={[item.basis === "model_only" ? MODEL_ONLY : null, item.note]}
              />
            )),
            ...report.well.map((item) => (
              <Item
                key={item.text}
                text={item.text}
                notes={[item.verified ? ITS_READING : NOT_VERIFIED]}
              />
            )),
          ]}
        </ReportList>
        <ReportList title="Went wrong">
          {facts.failed.map((item) => (
            <Item
              key={item.case}
              text={`${item.name}: ${(item.verdict !== "flaky" && item.label ? item.label : verdictWord(item.verdict)).toLowerCase()}${item.reason ? ` · ${item.reason}` : ""}`}
              onWhy={() => onTest(item.case, report.run)}
            />
          ))}
        </ReportList>
        <ReportList title={verdictWord("unjudged")}>
          {facts.unjudged.map((item) => (
            <Item
              key={item.case}
              text={`${item.name} · ${item.label ? `${item.label}: ` : ""}${unjudgedWords(item.reasons)}`}
              onWhy={() => onTest(item.case, report.run)}
            />
          ))}
        </ReportList>
        <ReportList title={ITS_READING}>
          {[
            ...report.reading.map((item) => (
              <Item
                key={item.text}
                text={item.text}
                notes={[item.verified ? null : NOT_VERIFIED]}
                onWhy={item.case ? () => onTest(item.case ?? "", report.run) : undefined}
              />
            )),
            ...report.next.map((item) => (
              <Item
                key={item.text}
                text={item.text}
                notes={[
                  item.verified
                    ? null
                    : item.refs.length || item.quotes.length
                      ? NOT_VERIFIED
                      : SUGGESTION,
                ]}
              />
            )),
          ]}
        </ReportList>
      </div>
      <form onSubmit={send} className="flex items-center gap-2 rounded-xl bg-bubble px-3.5 py-2">
        <input
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder={`Ask about ${label}`}
          aria-label={`Ask about ${label}`}
          className="min-w-0 flex-1 bg-transparent text-[14px] outline-none placeholder:text-ink-3"
        />
        <Button type="submit" size="icon-xs" aria-label="Send" disabled={!text.trim()}>
          <ArrowUp />
        </Button>
      </form>
    </section>
  );
}

/** A line of the report, kept to two lines until asked; its notes and link stay in view. */
function Item({
  text,
  notes = [],
  onWhy,
}: {
  text: string;
  notes?: (string | null)[];
  onWhy?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [long, setLong] = useState(false);
  const clamped = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const line = clamped.current;
    if (!line || open) return;
    const measure = () => setLong(line.scrollHeight > line.clientHeight + 1);
    measure();
    const watch = new ResizeObserver(measure);
    watch.observe(line);
    return () => watch.disconnect();
  }, [open]);
  const shownNotes = notes.filter(Boolean);
  return (
    <li className="flex flex-col gap-0.5">
      <span ref={clamped} className={cn("[overflow-wrap:anywhere]", !open && "line-clamp-2")}>
        {text}
      </span>
      {long || onWhy || shownNotes.length ? (
        <span className="flex flex-wrap items-baseline gap-x-2.5 text-[12px] text-ink-3">
          {shownNotes.map((note) => (
            <span key={note}>{note}</span>
          ))}
          {long ? (
            <TextLink aria-expanded={open} onClick={() => setOpen(!open)}>
              {open ? "Less" : "More"}
            </TextLink>
          ) : null}
          {onWhy ? <TextLink onClick={onWhy}>See why</TextLink> : null}
        </span>
      ) : null}
    </li>
  );
}

function ReportList({ title, children }: { title: string; children: ReactNode[] }) {
  if (!children.length) return null;
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <h3 className="m-0 text-[12px] font-semibold text-ink-3">{title}</h3>
      <ul className="m-0 flex list-none flex-col gap-3 p-0 text-[14px]">{children}</ul>
    </div>
  );
}

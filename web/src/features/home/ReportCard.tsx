import { ArrowUp } from "lucide-react";
import type { FormEvent, ReactNode } from "react";
import { useState } from "react";
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
      <h2 className="m-0 text-[20px] font-bold text-balance">{headline(facts)}</h2>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,240px),1fr))] gap-5">
        <ReportList title="Passed before, fails now">
          {facts.regressions.map((item) => (
            <li key={item.case}>
              {item.title} <SeeWhy onClick={() => onTest(item.case, report.run)} />
            </li>
          ))}
        </ReportList>
        <ReportList title="Went well">
          {[
            ...facts.held.map((item) => (
              <li key={item.case}>
                {`${item.name}: ${item.passed} of ${item.judged} tries`}
                {item.basis === "model_only" ? (
                  <span className="ms-1.5 text-[12px] text-ink-3">{MODEL_ONLY}</span>
                ) : null}
                {item.note ? (
                  <span className="ms-1.5 text-[12px] text-ink-3">{item.note}</span>
                ) : null}
              </li>
            )),
            ...report.well.map((item) => (
              <li key={item.text}>
                {item.text}
                <span className="ms-1.5 text-[12px] text-ink-3">
                  {item.verified ? ITS_READING : NOT_VERIFIED}
                </span>
              </li>
            )),
          ]}
        </ReportList>
        <ReportList title="Went wrong">
          {facts.failed.map((item) => (
            <li key={item.case}>
              {`${item.name}: ${(item.verdict !== "flaky" && item.label ? item.label : verdictWord(item.verdict)).toLowerCase()}`}
              {item.reason ? ` · ${item.reason}` : ""}{" "}
              <SeeWhy onClick={() => onTest(item.case, report.run)} />
            </li>
          ))}
        </ReportList>
        <ReportList title={verdictWord("unjudged")}>
          {facts.unjudged.map((item) => (
            <li key={item.case}>
              {`${item.name} · ${item.label ? `${item.label}: ` : ""}${unjudgedWords(item.reasons)}`}{" "}
              <SeeWhy onClick={() => onTest(item.case, report.run)} />
            </li>
          ))}
        </ReportList>
        <ReportList title={ITS_READING}>
          {[
            ...report.reading.map((item) => (
              <li key={item.text}>
                {item.text}
                {item.verified ? null : (
                  <span className="ms-1.5 text-[12px] text-ink-3">{NOT_VERIFIED}</span>
                )}
                {item.case ? (
                  <>
                    {" "}
                    <SeeWhy onClick={() => onTest(item.case ?? "", report.run)} />
                  </>
                ) : null}
              </li>
            )),
            ...report.next.map((item) => (
              <li key={item.text}>
                {item.text}
                {item.verified ? null : (
                  <span className="ms-1.5 text-[12px] text-ink-3">
                    {item.refs.length || item.quotes.length ? NOT_VERIFIED : SUGGESTION}
                  </span>
                )}
              </li>
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

function SeeWhy({ onClick }: { onClick: () => void }) {
  return <TextLink onClick={onClick}>See why</TextLink>;
}

function ReportList({ title, children }: { title: string; children: ReactNode[] }) {
  if (!children.length) return null;
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <h3 className="m-0 text-[12px] font-semibold text-ink-3">{title}</h3>
      <ul className="m-0 flex list-none flex-col gap-1.5 p-0 text-[14px]">{children}</ul>
    </div>
  );
}

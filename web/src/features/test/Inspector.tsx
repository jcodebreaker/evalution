import { X } from "lucide-react";
import type { ReactNode } from "react";
import { seconds } from "../../lib/format";
import type { Span } from "../../lib/schema";
import { Button } from "../../ui/button";

/** One step of the timeline: when, how long, the model and its tokens, and what went in and out. */
export function Inspector({ span, onClose }: { span: Span; onClose: () => void }) {
  const rows: [string, ReactNode][] = [
    ["Started", seconds(span.start, true)],
    ["Took", seconds(span.seconds, true)],
  ];
  if (span.model) rows.push(["Model", span.model]);
  if (span.tokens_in !== undefined || span.tokens_out !== undefined) {
    const tokensIn = (span.tokens_in ?? 0).toLocaleString();
    const tokensOut = (span.tokens_out ?? 0).toLocaleString();
    rows.push(["Tokens", `${tokensIn} in · ${tokensOut} out`]);
  }
  if (span.helper) rows.push(["Ran in", "A helper"]);
  if (span.automatic) rows.push(["Scored", "No, it ran by itself"]);
  if (span.check) {
    rows.push([
      "Check",
      <span key="check" className="text-destructive">
        {span.check}
      </span>,
    ]);
  }
  return (
    <aside
      aria-label="Step details"
      className="flex min-h-0 w-[300px] shrink-0 flex-col overflow-auto border-s border-separator bg-content max-lg:w-auto max-lg:border-s-0 max-lg:border-t"
    >
      <div className="flex items-start gap-2 px-4 pt-4 pb-2.5">
        <h4 className="m-0 flex-1 text-[13px] font-semibold break-words">{span.name}</h4>
        <Button variant="ghost" size="icon-xs" aria-label="Close" onClick={onClose}>
          <X />
        </Button>
      </div>
      <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-3.5 gap-y-1.5 px-4 pb-3.5 text-[12px]">
        {rows.map(([name, value]) => (
          <div key={name} className="contents">
            <dt className="text-ink-3">{name}</dt>
            <dd className="m-0 text-end tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
      {span.input || span.output ? (
        <div className="px-4 pb-4">
          {span.input ? <IO label="Input" text={span.input} /> : null}
          {span.output ? <IO label="Output" text={span.output} /> : null}
        </div>
      ) : null}
    </aside>
  );
}

function IO({ label, text }: { label: string; text: string }) {
  return (
    <>
      <h5 className="mt-2.5 mb-1 text-[11px] font-semibold text-ink-3">{label}</h5>
      <pre className="m-0 rounded-[7px] bg-window px-2.5 py-2 font-mono text-[11.5px] leading-[1.55] break-words whitespace-pre-wrap">
        {text}
      </pre>
    </>
  );
}

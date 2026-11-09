import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import type { Compare } from "../../lib/schema";
import { SectionHead } from "./SectionHead";

/** `text` with each highlighted phrase marked, green when it helped and red when it failed. */
function Marked({ text, marks }: { text: string; marks: { text: string; good: boolean }[] }) {
  const parts: ReactNode[] = [];
  let rest = text;
  let key = 0;
  while (rest) {
    let first: { at: number; mark: { text: string; good: boolean } } | null = null;
    for (const mark of marks) {
      const at = mark.text ? rest.indexOf(mark.text) : -1;
      if (at >= 0 && (!first || at < first.at)) first = { at, mark };
    }
    if (!first) {
      parts.push(rest);
      break;
    }
    if (first.at) parts.push(rest.slice(0, first.at));
    parts.push(
      <mark
        key={key++}
        className={cn(
          "rounded px-0.5 text-inherit",
          first.mark.good ? "bg-pass-soft" : "bg-fail-soft",
        )}
      >
        {first.mark.text}
      </mark>,
    );
    rest = rest.slice(first.at + first.mark.text.length);
  }
  return <>{parts}</>;
}

/** The reply against what the test wants of it. */
export function Output({ shown }: { shown: Compare }) {
  const output = shown.output;
  if (!output) return null;
  return (
    <section className="flex flex-col gap-2">
      <SectionHead title="Output" sub="what it said vs what it should say" />
      <div className="group-card grid grid-cols-2 max-md:grid-cols-1">
        <div className="p-3.5">
          <h4 className="m-0 mb-1.5 text-[12px] font-medium text-ink-3">
            What it said{shown.tries.length > 1 ? ` (try ${shown.try})` : ""}
          </h4>
          <p className="m-0 text-[13.5px] whitespace-pre-wrap">
            {output.reply ? <Marked text={output.reply} marks={output.highlights} /> : "No reply"}
          </p>
        </div>
        <div className="border-separator p-3.5 md:border-s max-md:border-t">
          <h4 className="m-0 mb-1.5 text-[12px] font-medium text-ink-3">What it should say</h4>
          {output.reference ? (
            <p className="m-0 text-[13.5px] text-ink-2 whitespace-pre-wrap">{output.reference}</p>
          ) : null}
          {output.must.length ? (
            <ul className="m-0 mt-1 flex list-disc flex-col gap-0.5 ps-4 text-[14px] text-ink-2">
              {output.must.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>
    </section>
  );
}

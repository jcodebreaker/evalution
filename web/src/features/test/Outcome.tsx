import { cn } from "../../lib/cn";
import type { Actual } from "../../lib/schema";
import { checkWord, NOT_VERIFIED } from "../../lib/verdict";
import { TextLink } from "../shared/group";
import { ToolChip } from "../shared/ToolChip";

/** What happened: the outcome in a word or a line, and the calls it saw. */
export function Outcome({
  actual,
  names,
  onSpan,
}: {
  actual: Actual;
  names?: Record<string, string>;
  onSpan?: (span: string) => void;
}) {
  return (
    <div className="mt-1 text-[12.5px] text-ink-2">
      <span
        className={cn(
          "font-semibold",
          actual.unjudged ? "text-ink-2" : actual.passed ? "text-success" : "text-destructive",
        )}
      >
        {actual.unjudged
          ? `${checkWord(actual)}${actual.text ? `: ${actual.text}` : ""}`
          : actual.text || checkWord(actual)}
      </span>
      {onSpan
        ? actual.spans.map((span) => (
            <TextLink key={span} className="ms-2" onClick={() => onSpan(span)}>
              Evidence
            </TextLink>
          ))
        : null}
      {actual.quotes.map((quote, index) => (
        <span key={index} className="mt-1 block border-s-2 border-separator ps-2 text-ink-2">
          “{quote.text}”
          {quote.verified === false ? (
            <span className="ms-1.5 text-warning">{NOT_VERIFIED}</span>
          ) : null}
        </span>
      ))}
      {actual.reply ? (
        <details className="group/reply mt-1.5">
          <summary className="line-clamp-2 cursor-default list-none whitespace-pre-wrap text-foreground group-open/reply:line-clamp-none [&::-webkit-details-marker]:hidden">
            {actual.reply}
          </summary>
        </details>
      ) : null}
      {actual.calls.length ? (
        <span className="mt-1 flex flex-wrap gap-1">
          {actual.calls.map((call, index) => (
            <ToolChip
              key={index}
              label={call}
              title={names?.[call.split("(")[0] ?? ""]}
              tone={actual.passed ? "plain" : "wrong"}
            />
          ))}
        </span>
      ) : null}
    </div>
  );
}

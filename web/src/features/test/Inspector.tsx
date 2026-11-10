import { Sparkles } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import { count, seconds } from "../../lib/format";
import { beforeWords } from "../../lib/isolation";
import type { Compare, Span } from "../../lib/schema";
import { Button } from "../../ui/button";
import { Kv, Label, Panel, PanelHead, Tag } from "../shared/kit";
import type { Entry, StepRow } from "./one-list";
import { pageLines } from "./Page";
import { CallName, frameWords, OutcomeIcon, rowLabel, Thumb } from "./StepList";

const STEP_SAID: Record<string, string> = {
  matched: "Matched",
  missing: "Not called",
  wrong_args: "Called with the wrong arguments",
  late: "Called late",
  never: "Never to be called",
  extra: "Not expected",
  unjudged: "Couldn't judge",
  automatic: "Ran by itself, not scored",
};

/** Who made a call, in words. */
const actorWords = (actor: string | null) =>
  !actor || actor === "main" ? "The assistant" : actor === "helper" ? "A helper" : actor;

function Text({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label>{label}</Label>
      <pre className="m-0 max-h-[320px] overflow-auto rounded-[10px] bg-sunk px-3.5 py-3 font-mono text-[13px] leading-relaxed [overflow-wrap:anywhere] whitespace-pre-wrap">
        {children}
      </pre>
    </div>
  );
}

/** One span of the trace: when, how long, the model and its tokens, and what went in and out. */
export function SpanFacts({ span }: { span: Span }) {
  const rows: [ReactNode, ReactNode][] = [
    ["Started", seconds(span.start, true)],
    ["Took", seconds(span.seconds, true)],
    ["Ran in", span.helper ? "A helper" : "The assistant"],
  ];
  if (span.model)
    rows.push([
      "Model",
      <span key="m" className="font-mono">
        {span.model}
      </span>,
    ]);
  if (span.tokens_in !== undefined || span.tokens_out !== undefined) {
    const tokensIn = (span.tokens_in ?? 0).toLocaleString();
    const tokensOut = (span.tokens_out ?? 0).toLocaleString();
    rows.push([
      "Tokens",
      <span key="t" className="font-mono">{`${tokensIn} in · ${tokensOut} out`}</span>,
    ]);
  }
  if (span.automatic) rows.push(["Scored", "No, it ran by itself"]);
  if (span.check)
    rows.push([
      "Check",
      <span key="c" className="text-fail">
        {span.check}
      </span>,
    ]);
  return (
    <div className="flex flex-col gap-3.5">
      <Kv rows={rows} />
      {span.input ? <Text label="Input">{span.input}</Text> : null}
      {span.output ? <Text label="Output">{span.output}</Text> : null}
    </div>
  );
}

/** A span picked on the trace, in the inspector beside it. */
export function SpanInspector({ span, onAsk }: { span: Span; onAsk?: () => void }) {
  return (
    <Panel aria-label="Inspector" className="sticky top-4">
      <PanelHead>
        <Tag
          tone={
            span.check
              ? "bad"
              : span.kind === "tool"
                ? "tool"
                : span.kind === "model"
                  ? "llm"
                  : "agent"
          }
        >
          {span.name}
        </Tag>
        <span className="flex-1" />
        <span className="font-mono text-xs text-ink-3">{seconds(span.start, true)}</span>
      </PanelHead>
      <div className="flex flex-col gap-3.5 p-5">
        <SpanFacts span={span} />
        {onAsk ? (
          <Button variant="secondary" className="self-start" onClick={onAsk}>
            <Sparkles />
            Ask why
          </Button>
        ) : null}
      </div>
    </Panel>
  );
}

function CallFacts({ entry, span }: { entry: Entry; span: Span | undefined }) {
  const rows: [ReactNode, ReactNode][] = [];
  if (entry.expected)
    rows.push([
      "Expected",
      <code key="e" className="font-mono text-[13px]">
        <CallName text={entry.expected} />
      </code>,
    ]);
  rows.push([
    "Made",
    entry.made ? (
      <code key="m" className="font-mono text-[13px]">
        <CallName text={entry.made} />
      </code>
    ) : (
      "Not called"
    ),
  ]);
  if (entry.step) {
    rows.push([
      "Scored",
      <span key="s" className={cn(entry.status === "failed" && "text-fail")}>
        {STEP_SAID[entry.step] ?? entry.step}
      </span>,
    ]);
  }
  rows.push(["Ran in", actorWords(entry.actor)]);
  if (entry.reason) rows.push(["Why", entry.reason]);
  return (
    <div className="flex flex-col gap-3.5 border-rule not-first:border-t not-first:pt-3.5">
      <Kv rows={rows} />
      {span ? <SpanFacts span={span} /> : null}
    </div>
  );
}

/** What the inspector says of a row, by its kind. */
function RowFacts({ row, shown }: { row: StepRow; shown: Compare }) {
  const first = row.entries[0];
  if (!first) return null;
  const spans = shown.detail?.timeline ?? [];
  switch (row.kind) {
    case "calls":
      return (
        <>
          {row.entries.map((entry, index) => (
            <CallFacts
              key={index}
              entry={entry}
              span={entry.span ? spans.find((span) => span.id === entry.span) : undefined}
            />
          ))}
        </>
      );
    case "reply": {
      const frame = row.entries.find((entry) => entry.kind === "frame");
      const output = shown.output;
      const last = first.lines.length > 0;
      return (
        <>
          <Text label="What it said">{first.text || "No reply"}</Text>
          {first.error ? <p className="m-0 text-sm text-fail">{first.error}</p> : null}
          {first.lines.length ? (
            <div className="flex flex-col gap-2">
              <Label>Held to</Label>
              {first.lines.map((line) => (
                <div key={line.id} className="flex items-start gap-2 text-sm">
                  <OutcomeIcon status={line.status} className="mt-0.5" />
                  <span className="flex min-w-0 flex-col">
                    <span>{line.text}</span>
                    {line.reason ? <span className="text-ink-3">{line.reason}</span> : null}
                  </span>
                </div>
              ))}
            </div>
          ) : null}
          {last && output && (output.reference || output.must.length) ? (
            <div className="flex flex-col gap-1.5">
              <Label>What it should say</Label>
              {output.reference ? (
                <p className="m-0 text-sm whitespace-pre-wrap text-ink-2">{output.reference}</p>
              ) : null}
              {output.must.length ? (
                <ul className="m-0 flex list-disc flex-col gap-0.5 ps-4 text-sm text-ink-2">
                  {output.must.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : null}
          {frame ? <FrameFacts entry={frame} /> : null}
        </>
      );
    }
    case "frame":
      return <FrameFacts entry={first} />;
    case "message":
      return (
        <Text label={first.filler ? "Ordinary chat" : `Message ${first.turn ?? ""}`.trim()}>
          {first.text ?? ""}
        </Text>
      );
    case "state":
      return (
        <>
          {row.entries.map((entry, index) => (
            <Kv
              key={index}
              rows={[
                ["Checks", entry.key ?? ""],
                [
                  "Found",
                  <span key="f" className={cn(entry.status === "failed" && "text-fail")}>
                    {entry.made ?? (entry.status === "passed" ? "As expected" : "–")}
                  </span>,
                ],
                ...(entry.reason ? ([["Why", entry.reason]] as [ReactNode, ReactNode][]) : []),
              ]}
            />
          ))}
        </>
      );
    case "before": {
      const words = shown.isolation ? beforeWords(shown.isolation, first.text) : null;
      const files = shown.steps.find((step) => step.kind === "before")?.files ?? [];
      return (
        <>
          <p className="m-0">{words?.title ?? first.text ?? "Started"}</p>
          {words?.line ? <p className="m-0 text-sm text-ink-2">{words.line}</p> : null}
          {words?.warning ? (
            <p className="m-0 text-sm font-medium text-unj">{words.warning}</p>
          ) : null}
          {files.map((file) => (
            <Text
              key={file.name}
              label={`${file.name}${file.rows === undefined ? "" : ` · ${count(file.rows, "row")}`}`}
            >
              {file.peek ?? file.columns?.join(", ") ?? ""}
              {file.more ? `\n… ${file.more} more` : ""}
            </Text>
          ))}
        </>
      );
    }
    case "health": {
      const lines = shown.page ? pageLines(shown.page) : [];
      return (
        <>
          <p className={cn("m-0 text-sm", first.status === "failed" ? "text-fail" : "text-ink-2")}>
            {first.status === "passed"
              ? "No console errors, no failed requests"
              : (first.why ?? "")}
          </p>
          {lines.length ? (
            <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
              {lines.slice(0, 8).map((line, index) => (
                <li key={index} className="flex items-baseline gap-2 text-sm">
                  <span className={line.tone === "error" ? "text-fail" : "text-unj"}>
                    {line.tone === "error" ? "Error" : "Warning"}
                  </span>
                  <code className="min-w-0 font-mono text-[13px] [overflow-wrap:anywhere]">
                    {line.text}
                  </code>
                </li>
              ))}
            </ul>
          ) : null}
        </>
      );
    }
    case "break":
      return (
        <p className="m-0 text-ink-2">{first.text || "A new conversation, the same person"}</p>
      );
  }
}

function FrameFacts({ entry }: { entry: Entry }) {
  const said = (entry.text ?? "").trim() || (entry.instead ?? "").trim();
  return (
    <div className="flex flex-col gap-2">
      <Label>On the page</Label>
      <Thumb entry={entry} wide />
      <span className={cn("text-sm", entry.status === "failed" ? "text-fail" : "text-ink-2")}>
        {frameWords(entry)}
        {entry.why && entry.status !== "passed" ? ` · ${entry.why}` : ""}
      </span>
      {entry.part ? (
        <span className="text-sm text-ink-3">
          {"Missing: "}
          <code className="font-mono">{entry.part.missing}</code>
        </span>
      ) : said ? (
        <p className="m-0 line-clamp-4 text-sm whitespace-pre-wrap text-ink-3">{said}</p>
      ) : null}
    </div>
  );
}

/** The row picked in "Step by step", beside it: everything kept of it. */
export function StepInspector({
  row,
  index,
  shown,
  appName,
  onAsk,
}: {
  row: StepRow;
  index: number;
  shown: Compare;
  appName: string;
  onAsk?: (text: string) => void;
}) {
  const label = `Step ${index + 1} · ${rowLabel(row, appName)}`;
  return (
    <Panel aria-label="Inspector" className="sticky top-4">
      <PanelHead title={label}>
        <span className="flex-1" />
        <span className="font-mono text-xs text-ink-3">
          {row.at === null ? "" : seconds(row.at, true)}
        </span>
      </PanelHead>
      <div className="flex flex-col gap-3.5 p-5">
        <RowFacts row={row} shown={shown} />
        {onAsk && row.status === "failed" ? (
          <Button
            variant="secondary"
            className="self-start"
            onClick={() =>
              onAsk(
                `Explain step ${index + 1} (${rowLabel(row, appName)}) of "${shown.name}", try ${shown.try ?? 1}.`,
              )
            }
          >
            <Sparkles />
            Ask about this step
          </Button>
        ) : null}
      </div>
    </Panel>
  );
}

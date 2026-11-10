import { Check, Minus, X } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import { seconds } from "../../lib/format";
import { beforeWords } from "../../lib/isolation";
import type { LineAgreement, TimelineLine, TryIsolation, TryOutcome } from "../../lib/schema";
import { unjudgedWords } from "../../lib/verdict";
import { Chip, Tag } from "../shared/kit";
import { AGREE_KEYS, Agree } from "./Agree";
import { agreementKey, type Entry, isUi, type StepRow } from "./one-list";

// "Step by step": the try as one time-ordered list, each row with its mark in a gutter line,
// clicked to inspect it beside.

/** What a call's step says, as a chip, when it says more than matched or missing. */
const STEP_CHIP: Record<string, string> = {
  wrong_args: "wrong args",
  late: "late",
  extra: "extra",
  unjudged: "couldn't judge",
  automatic: "automatic",
};

/** The row's name, by its kind. */
export function rowLabel(row: StepRow, app: string): string {
  switch (row.kind) {
    case "before":
      return "Before";
    case "message":
      return row.entries[0]?.filler ? "Ordinary chat" : "The person says";
    case "calls":
      return `What ${app} did`;
    case "reply":
      return "What the reply said";
    case "frame":
      return "What the page showed";
    case "state":
      return "What the app holds";
    case "break":
      return "Break";
    case "health":
      return "Page healthy";
    case "ui":
      return "On the page";
  }
}

/** A UI check's key in the list: its id, else its place in its row. */
export const uiKey = (entry: Entry, index: number) => entry.id ?? `${entry.kind}:${index}`;

/** What a UI check found, in plain words; one not judged says why. */
export function uiWords(entry: Entry): string {
  if (entry.status === "unjudged") {
    const plain = entry.unjudged ? unjudgedWords([entry.unjudged]) : null;
    const why = plain && plain !== entry.unjudged ? plain : entry.why || plain;
    return why ? `Couldn't judge: ${why}` : "Couldn't judge";
  }
  return entry.why || entry.text || "";
}

/** A turn's UI check results, each picked to see the turn's screenshot beside; a model's look
 * carries the "model" tag and agree and disagree. */
function UiResults({
  entries,
  picked,
  onPick,
  agreement,
  onLabel,
}: {
  entries: Entry[];
  picked?: string | null;
  onPick?: (key: string) => void;
  agreement?: Record<string, LineAgreement>;
  onLabel?: (line: string, agree: boolean | null) => void;
}) {
  if (!entries.length) return null;
  return (
    <div className="flex flex-col gap-1">
      {entries.map((entry, index) => {
        const key = uiKey(entry, index);
        const look = entry.kind === "page_look";
        const said = look && entry.id ? agreement?.[entry.id] : undefined;
        const answer = said && onLabel && entry.id ? said : undefined;
        return (
          <div
            key={key}
            data-testid="ui-result"
            data-status={entry.status ?? "none"}
            className={cn(
              "flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 rounded-md",
              picked === key && "bg-tint-soft",
            )}
          >
            <button
              type="button"
              aria-pressed={picked === key}
              className="flex min-w-0 items-start gap-2 text-start focus-visible:outline-2 focus-visible:outline-tint"
              onClick={(event) => {
                event.stopPropagation();
                onPick?.(key);
              }}
            >
              <OutcomeIcon status={entry.status} className="mt-[3px]" />
              <span
                className={cn(
                  "min-w-0 [overflow-wrap:anywhere]",
                  entry.status === "failed" && "text-fail",
                  entry.status === "unjudged" && "text-ink-3",
                )}
              >
                {uiWords(entry)}
              </span>
            </button>
            {look ? <Chip className="h-6">model</Chip> : null}
            {answer ? (
              // biome-ignore lint/a11y/noStaticElementInteractions: keeps the row's click off the thumbs
              // biome-ignore lint/a11y/useKeyWithClickEvents: the thumbs are buttons of their own
              <span className="ms-auto" onClick={(event) => event.stopPropagation()}>
                <Agree
                  agreement={answer}
                  onPress={(agree) =>
                    onLabel?.(entry.id ?? "", answer.mine === agree ? null : agree)
                  }
                />
              </span>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

/** An outcome as a small tick, cross or dash. */
export function OutcomeIcon({
  status,
  className,
}: {
  status: TryOutcome | null;
  className?: string;
}) {
  if (status === "passed")
    return <Check aria-label="Held" className={cn("size-[15px] shrink-0 text-pass", className)} />;
  if (status === "failed")
    return <X aria-label="Failed" className={cn("size-[15px] shrink-0 text-fail", className)} />;
  if (status === "unjudged")
    return (
      <Minus
        aria-label="Couldn't judge"
        className={cn("size-[15px] shrink-0 text-unj", className)}
      />
    );
  return <Minus aria-hidden className={cn("size-[15px] shrink-0 text-ink-3", className)} />;
}

/** A call's name, with the MCP server it was named on (`crm__search`) kept apart. */
export function CallName({ text }: { text: string }) {
  const at = text.indexOf("__");
  if (at <= 0 || (text.indexOf("(") > -1 && text.indexOf("(") < at)) return <>{text}</>;
  return (
    <>
      <span className="opacity-60">{text.slice(0, at)}·</span>
      {text.slice(at + 2)}
    </>
  );
}

/** `text` with each highlighted phrase marked: underlined green where it helped, red where not. */
export function Marked({
  text,
  marks,
}: {
  text: string;
  marks: { text: string; good: boolean }[];
}) {
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
          "bg-transparent text-inherit",
          first.mark.good
            ? "border-b-2 border-pass"
            : "underline decoration-fail decoration-wavy decoration-2 underline-offset-4",
        )}
      >
        {first.mark.text}
      </mark>,
    );
    rest = rest.slice(first.at + first.mark.text.length);
  }
  return <>{parts}</>;
}

/** A screenshot of the page as a small frame, or its outline when none was kept. */
export function Thumb({ entry, wide }: { entry: Entry; wide?: boolean }) {
  const bad = entry.status === "failed";
  return (
    <span
      className={cn(
        "relative block aspect-[16/10] shrink-0 overflow-hidden rounded-[9px] border bg-sunk",
        wide ? "w-[190px]" : "w-[132px]",
        bad ? "border-fail shadow-[0_0_0_3px_var(--fail-soft)]" : "border-rule",
      )}
    >
      {entry.shot_url ? (
        <img
          src={entry.shot_url}
          alt={`The page after message ${entry.turn ?? ""}`.trim()}
          loading="lazy"
          className="block size-full object-cover object-top"
        />
      ) : (
        <span aria-hidden className="absolute inset-x-0 top-0 h-[16%] bg-sunk-2" />
      )}
    </span>
  );
}

/** What the page showed of a reply, in a few words. */
export function frameWords(entry: Entry): string {
  if (entry.status === "failed") return entry.part ? "Shown only in part" : "Not shown";
  if (entry.status === "unjudged") return "Nothing to show";
  return entry.shown_at === null ? "Shown" : `Shown at ${seconds(entry.shown_at, true)}`;
}

function Frame({ entry }: { entry: Entry }) {
  return (
    <div className="flex items-center gap-2.5">
      <Thumb entry={entry} />
      <span
        className={cn(
          "flex items-center gap-1.5 text-sm",
          entry.status === "failed" ? "text-fail" : "text-ink-3",
        )}
      >
        <OutcomeIcon status={entry.status} />
        {frameWords(entry)}
      </span>
    </div>
  );
}

/** One line a reply was held to, with agree and disagree when the grader decided it. */
function Line({
  line,
  agreement,
  onLabel,
}: {
  line: TimelineLine;
  agreement?: LineAgreement;
  onLabel?: (agree: boolean | null) => void;
}) {
  const answerable = agreement && onLabel ? agreement : undefined;
  const press = (agree: boolean) => onLabel?.(answerable?.mine === agree ? null : agree);
  const Row = answerable ? "fieldset" : "div";
  return (
    // A graded line takes focus: one key agrees or disagrees with the grader.
    <Row
      aria-label={answerable ? line.text : undefined}
      tabIndex={answerable?.can ? 0 : undefined}
      aria-keyshortcuts={answerable?.can ? Object.keys(AGREE_KEYS).join(" ") : undefined}
      className="m-0 flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1 rounded-md border-0 p-0 outline-none focus-visible:bg-tint-soft"
      onClick={(event) => event.stopPropagation()}
      onKeyDown={
        answerable?.can
          ? (event) => {
              const agree = AGREE_KEYS[event.key];
              if (agree !== undefined && !event.metaKey && !event.ctrlKey && !event.altKey) {
                event.preventDefault();
                event.stopPropagation();
                press(agree);
              }
            }
          : undefined
      }
    >
      <OutcomeIcon status={line.status} />
      <span className="min-w-0">{line.text}</span>
      {line.by ? <Chip>{line.by === "code" ? "checked exactly" : line.by}</Chip> : null}
      {line.role === "guide" ? <Chip>guide</Chip> : null}
      {answerable ? (
        <span className="ms-auto">
          <Agree agreement={answerable} onPress={press} />
        </span>
      ) : null}
    </Row>
  );
}

function Calls({ entries }: { entries: Entry[] }) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_22px_minmax(0,1fr)] items-center gap-x-2 gap-y-2 max-md:grid-cols-1">
      <h3 className="m-0 text-xs font-medium text-ink-3 max-md:hidden">Expected</h3>
      <span className="max-md:hidden" />
      <h3 className="m-0 text-xs font-medium text-ink-3 max-md:hidden">What it did</h3>
      {entries.map((entry, index) => {
        const step = entry.step ?? "";
        const failed = entry.status === "failed";
        const delegated = entry.actor && !["main", "person", "page", "code"].includes(entry.actor);
        return (
          <div key={index} className="contents" data-testid="call">
            <span className="flex min-w-0 flex-wrap items-center gap-2">
              {entry.expected ? (
                <>
                  <Tag
                    tone={step === "missing" ? "miss" : "tool"}
                    never={step === "never"}
                    title={entry.expected}
                  >
                    <CallName text={entry.expected} />
                  </Tag>
                  {step === "never" ? <Chip>Never</Chip> : null}
                </>
              ) : (
                <span className="text-sm text-ink-3">{entry.automatic ? "by itself" : "–"}</span>
              )}
            </span>
            <OutcomeIcon status={entry.status} />
            <span className="flex min-w-0 flex-wrap items-center gap-2">
              {entry.made ? (
                <Tag
                  tone={failed ? "bad" : entry.status === "passed" ? "tool" : "plain"}
                  title={entry.made}
                >
                  <CallName text={entry.made} />
                </Tag>
              ) : (
                <span className="text-sm text-ink-3">not called</span>
              )}
              {STEP_CHIP[step] ? (
                <Chip tone={failed ? "no" : "plain"}>{STEP_CHIP[step]}</Chip>
              ) : null}
              {delegated ? <Tag tone="agent">{entry.actor}</Tag> : null}
            </span>
            {entry.reason && (failed || delegated || entry.status === "unjudged") ? (
              <span
                className={cn(
                  "col-span-3 -mt-1 text-sm max-md:col-span-1",
                  failed ? "text-fail" : "text-ink-3",
                )}
              >
                {entry.reason}
              </span>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

/** The body of a row: the words, calls, lines and frames it holds. */
export function RowBody({
  row,
  isolation,
  agreement,
  onLabel,
  highlights,
  pickedCheck,
  onPickCheck,
}: {
  row: StepRow;
  isolation?: TryIsolation | null;
  agreement?: Record<string, LineAgreement>;
  onLabel?: (line: string, agree: boolean | null) => void;
  highlights?: { text: string; good: boolean }[];
  /** The UI check picked in this row, by {@link uiKey}. */
  pickedCheck?: string | null;
  onPickCheck?: (key: string) => void;
}) {
  const first = row.entries[0];
  if (!first) return null;
  const ui = (
    <UiResults
      entries={row.entries.filter(isUi)}
      picked={pickedCheck}
      onPick={onPickCheck}
      agreement={agreement}
      onLabel={onLabel}
    />
  );
  switch (row.kind) {
    case "before": {
      const words = isolation ? beforeWords(isolation, first.text) : null;
      return (
        <div className="flex flex-col gap-0.5" data-testid="before-isolation">
          <span>{words?.title ?? (first.text || "Started")}</span>
          {words?.line ? <span className="text-sm text-ink-3">{words.line}</span> : null}
          {words?.warning || first.problem ? (
            <span className="text-sm font-medium text-unj">{words?.warning ?? first.problem}</span>
          ) : null}
        </div>
      );
    }
    case "message":
      return (
        <span className="w-fit max-w-full rounded-[16px_16px_16px_5px] bg-sunk px-3.5 py-2 whitespace-pre-wrap [overflow-wrap:anywhere]">
          {first.text || "A message"}
        </span>
      );
    case "calls":
      return <Calls entries={row.entries} />;
    case "reply": {
      const frame = row.entries.find((entry) => entry.kind === "frame");
      return (
        <>
          {first.text ? (
            <span className="w-fit max-w-full rounded-[16px_16px_5px_16px] bg-tint-soft px-3.5 py-2 whitespace-pre-wrap [overflow-wrap:anywhere]">
              {highlights?.length && first.lines.length ? (
                <Marked text={first.text} marks={highlights} />
              ) : (
                first.text
              )}
            </span>
          ) : (
            <span className="text-sm text-ink-3">No reply</span>
          )}
          {first.error ? <span className="text-sm text-fail">{first.error}</span> : null}
          {first.lines.length ? (
            <div className="flex flex-col gap-1.5">
              {first.lines.map((line) => (
                <Line
                  key={line.id}
                  line={line}
                  agreement={agreement?.[agreementKey(line.id)]}
                  onLabel={onLabel ? (agree) => onLabel(line.id, agree) : undefined}
                />
              ))}
            </div>
          ) : null}
          {frame ? <Frame entry={frame} /> : null}
          {ui}
        </>
      );
    }
    case "frame":
      return (
        <>
          <Frame entry={first} />
          {ui}
        </>
      );
    case "ui":
      return ui;
    case "state":
      return (
        <div className="flex flex-col gap-1.5">
          {row.entries.map((entry, index) => (
            <div key={index} className="flex flex-col gap-0.5">
              <span className="flex flex-wrap items-center gap-2">
                <OutcomeIcon status={entry.status} />
                <span>{entry.key}</span>
                {entry.actor === "code" ? <Chip>checked exactly</Chip> : null}
              </span>
              {entry.made && entry.status !== "passed" ? (
                <span
                  className={cn("text-sm", entry.status === "failed" ? "text-fail" : "text-ink-3")}
                >
                  {entry.made}
                </span>
              ) : null}
              {entry.reason ? <span className="text-sm text-ink-3">{entry.reason}</span> : null}
            </div>
          ))}
        </div>
      );
    case "break":
      return <span className="text-ink-3">{first.text || "New conversation"}</span>;
    case "health":
      return (
        <span className={cn("text-sm", first.status === "failed" ? "text-fail" : "text-ink-3")}>
          {first.status === "passed" ? "No console errors, no failed requests" : (first.why ?? "")}
        </span>
      );
  }
}

/**
 * The try step by step, one time-ordered list: what it started from, what the person said, each
 * expected call against the one made, each reply with its graded lines and what the page showed,
 * each state check, and the page's health. A row is picked to inspect it.
 */
export function StepList({
  rows,
  appName,
  selected,
  onSelect,
  isolation,
  agreement,
  onLabel,
  highlights,
  differs,
  pickedCheck,
  onPickCheck,
}: {
  rows: StepRow[];
  appName: string;
  selected: number | null;
  onSelect: (index: number) => void;
  isolation?: TryIsolation | null;
  agreement?: Record<string, LineAgreement>;
  onLabel?: (line: string, agree: boolean | null) => void;
  /** The last reply's words marked, good and bad. */
  highlights?: { text: string; good: boolean }[];
  /** The row where this try first differs from a passing one. */
  differs?: number | null;
  /** The UI check picked in the selected row, by {@link uiKey}. */
  pickedCheck?: string | null;
  /** A UI check picked: its row is selected, and its turn's screenshot shown beside. */
  onPickCheck?: (index: number, key: string) => void;
}) {
  const lastReply = rows.findLastIndex((row) => row.kind === "reply");
  return (
    <ol className="m-0 flex list-none flex-col py-2 ps-0" aria-label="Step by step">
      {rows.map((row, index) => {
        const bad = row.status === "failed";
        const square =
          row.kind === "state" ||
          row.kind === "health" ||
          row.kind === "before" ||
          row.kind === "ui";
        const label = `Step ${index + 1} · ${rowLabel(row, appName)}`;
        return (
          // biome-ignore lint/a11y/useKeyWithClickEvents: the row's own button takes the keyboard
          <li
            key={index}
            id={`step-row-${index}`}
            data-testid="step-row"
            data-status={row.status ?? "none"}
            aria-current={selected === index ? "true" : undefined}
            onClick={() => onSelect(index)}
            className={cn(
              "group/step relative grid scroll-mt-24 grid-cols-[52px_minmax(0,1fr)] hover:bg-sunk",
              bad && "bg-fail-soft hover:bg-fail-soft",
              selected === index &&
                (bad ? "shadow-[inset_4px_0_0_var(--fail)]" : "bg-tint-soft hover:bg-tint-soft"),
            )}
          >
            <span className="relative flex justify-center pt-4 before:absolute before:inset-y-0 before:left-1/2 before:-ml-px before:w-0.5 before:bg-rule group-first/step:before:top-5 group-last/step:before:bottom-[calc(100%-20px)]">
              <i
                aria-hidden
                className={cn(
                  "relative size-3.5 border-2 border-rule-strong bg-group",
                  square ? "rounded-[4px]" : "rounded-full",
                  row.status === "passed" && "border-pass bg-pass",
                  row.status === "failed" && "border-fail bg-fail",
                  row.status === "unjudged" && "border-unj bg-unj",
                )}
              />
            </span>
            <div className="flex min-w-0 flex-col gap-2 py-3 pe-5">
              <div className="flex items-center gap-2 text-[13px] text-ink-3">
                <button
                  type="button"
                  className="text-start hover:text-ink focus-visible:outline-2 focus-visible:outline-tint"
                  aria-pressed={selected === index}
                  onClick={(event) => {
                    event.stopPropagation();
                    onSelect(index);
                  }}
                >
                  {label}
                </button>
                {differs === index ? <Chip tone="warn">Differs from the passing try</Chip> : null}
                <span className="ms-auto font-mono text-xs tabular-nums">
                  {row.at === null ? "" : seconds(row.at, true)}
                </span>
              </div>
              <RowBody
                row={row}
                isolation={isolation}
                agreement={agreement}
                onLabel={onLabel}
                highlights={index === lastReply ? highlights : undefined}
                pickedCheck={selected === index ? pickedCheck : null}
                onPickCheck={(key) => (onPickCheck ? onPickCheck(index, key) : onSelect(index))}
              />
            </div>
          </li>
        );
      })}
    </ol>
  );
}

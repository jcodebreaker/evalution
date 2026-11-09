import { ChevronRight } from "lucide-react";
import { useState } from "react";
import { ApiError, api } from "../../lib/api";
import { count } from "../../lib/format";
import type { AppSkeleton as Found, SkeletonEntry } from "../../lib/schema";
import { ready, useLoad } from "../../lib/use-load";
import { Skeleton as Placeholder } from "../../ui/field";
import { Group, GroupHeading } from "../shared/group";
import { SECTION, SectionHeading } from "./SectionHeading";

/**
 * The app's skeleton (ADR 0012): what it is built from, read off its code with no model, so it
 * shows before any learn. Each part with its file and line, and how often real use touched it.
 */
const GROUPS: { kind: SkeletonEntry["kind"]; title: string }[] = [
  { kind: "entry", title: "Entry points" },
  { kind: "prompt", title: "Prompts" },
  { kind: "tool", title: "Tools" },
  { kind: "model", title: "Model calls" },
  { kind: "route", title: "Routes" },
  { kind: "state", title: "State" },
  { kind: "agent", title: "Agents" },
];

/** Rows a group shows before "Show more". */
const SHOWN = 8;

/** What matters first: the most used, then what the most places name; else as the code has it. */
export function ranked(entries: SkeletonEntry[]): SkeletonEntry[] {
  return entries
    .map((entry, at) => ({ entry, at }))
    .sort(
      (a, b) =>
        (b.entry.uses ?? 0) - (a.entry.uses ?? 0) ||
        b.entry.places.length - a.entry.places.length ||
        a.at - b.at,
    )
    .map(({ entry }) => entry);
}

/** When the use counted happened, in plain words: "this week". */
export function windowWords(window: Found["window"], now = Date.now() / 1000): string {
  if (!window) return "";
  if (!window.since) return `in ${count(window.sessions, "conversation")}`;
  const days = (now - window.since) / 86_400;
  if (days <= 1) return "today";
  if (days <= 7) return "this week";
  if (days <= 31) return "this month";
  return `in the last ${Math.ceil(days)} days`;
}

/** How often real use touched an entry, in plain words; "" when there's nothing to say. */
export function useWords(entry: SkeletonEntry, when: string): string {
  const n = entry.uses;
  if (n === null) return "";
  if (n === 0) {
    if (entry.kind === "tool") return `not used ${when}`;
    if (entry.kind === "prompt") return `not sent ${when}`;
    return "";
  }
  if (entry.kind === "prompt") return `sent ${n === 1 ? "once" : `${n} times`} ${when}`;
  if (entry.kind === "route") return `${count(n, "request")} ${when}`;
  return `${count(n, "call")} ${when}`;
}

/** What an entry is, beside its name: a tool's arguments, a model's call, a store's kind. */
function sub(entry: SkeletonEntry): string {
  switch (entry.kind) {
    case "prompt":
      return entry.what === "builder" ? "built in code" : "";
    case "tool":
      if (entry.agents) return `used by ${count(entry.agents, "agent")}`;
      return entry.what === "call" && entry.inside ? `used in ${entry.inside}` : "";
    case "model":
      if (entry.what === "setting")
        return entry.model_from ? `set as ${entry.model_from}` : "set in settings";
      return [
        entry.model ? entry.name : "",
        !entry.model && entry.model_from ? `model from ${entry.model_from}` : "",
        entry.inside ? `in ${entry.inside}` : "",
      ]
        .filter(Boolean)
        .join(" ");
    case "route":
      return entry.inside ? `served by ${entry.inside}` : "";
    case "state":
      return entry.what === entry.name.toLowerCase() ? "" : entry.what;
    case "agent":
      return entry.model ? `model ${entry.model}` : "";
    case "entry":
      return entry.what === "serves" ? "starts serving" : entry.what;
  }
}

function title(entry: SkeletonEntry): string {
  if (entry.kind === "tool" && entry.args.length) return `${entry.name}(${entry.args.join(", ")})`;
  if (entry.kind === "model" && entry.model) return entry.model;
  return entry.name;
}

function Row({ entry, when }: { entry: SkeletonEntry; when: string }) {
  const where = `${entry.path}:${entry.line}`;
  const used = useWords(entry, when);
  const said = sub(entry);
  return (
    <>
      <span
        className={
          entry.kind === "route" || entry.kind === "tool"
            ? "min-w-0 truncate font-mono text-[13px]"
            : "min-w-0 truncate text-[14px]"
        }
      >
        {title(entry)}
      </span>
      {said ? <span className="min-w-0 truncate text-[13px] text-ink-2">{said}</span> : null}
      <span
        title={[where, ...entry.places].join("\n")}
        className="ms-auto min-w-0 truncate font-mono text-[12px] text-ink-3"
      >
        {where}
        {entry.places.length ? (
          <span className="font-sans"> and {count(entry.places.length, "more place")}</span>
        ) : null}
      </span>
      {used ? (
        <span
          className={
            entry.uses
              ? "shrink-0 text-end text-[13px] text-ink-2 tabular-nums"
              : "shrink-0 text-end text-[13px] text-ink-3"
          }
        >
          {used}
        </span>
      ) : null}
    </>
  );
}

const ROW = "flex flex-wrap items-baseline gap-x-2 gap-y-0.5 px-[18px] py-[7px]";

function Entry({ entry, when }: { entry: SkeletonEntry; when: string }) {
  if (entry.kind !== "prompt" || !entry.text) {
    return (
      <li className={ROW}>
        {/* In line with the prompts that open. */}
        {entry.kind === "prompt" ? <span aria-hidden className="size-3 shrink-0" /> : null}
        <Row entry={entry} when={when} />
      </li>
    );
  }
  // A prompt's text opens under it.
  return (
    <li>
      <details className="group/prompt">
        <summary
          className={`${ROW} cursor-default list-none hover:bg-sunk/50 focus-visible:outline-2 focus-visible:outline-tint [&::-webkit-details-marker]:hidden`}
        >
          <ChevronRight
            aria-hidden
            className="size-3 shrink-0 self-center text-ink-3 transition-transform group-open/prompt:rotate-90"
          />
          <Row entry={entry} when={when} />
        </summary>
        <pre className="mx-[18px] mt-0.5 mb-2 max-h-72 overflow-y-auto whitespace-pre-wrap rounded-[8px] bg-sunk px-3 py-2 font-mono text-[12.5px] leading-[1.55]">
          {entry.text}
        </pre>
      </details>
    </li>
  );
}

function Kind({
  heading,
  entries,
  when,
}: {
  heading: string;
  entries: SkeletonEntry[];
  when: string;
}) {
  const [all, setAll] = useState(false);
  const shown = all ? entries : entries.slice(0, SHOWN);
  return (
    <div className={SECTION}>
      <SectionHeading>
        {heading} · {entries.length}
      </SectionHeading>
      <ul aria-label={heading} className="m-0 list-none p-0">
        {shown.map((entry) => (
          <Entry key={entry.id} entry={entry} when={when} />
        ))}
      </ul>
      {entries.length > shown.length ? (
        <button
          type="button"
          onClick={() => setAll(true)}
          className="mx-[18px] mt-1 rounded-md px-1 py-0.5 text-[13px] text-ink-2 hover:text-foreground focus-visible:outline-2 focus-visible:outline-tint"
        >
          Show {entries.length - shown.length} more
        </button>
      ) : null}
    </div>
  );
}

const KIND_WORDS: Record<string, string> = {
  tool: "tool",
  prompt: "prompt",
  model: "model",
  route: "route",
};

/** No code folder is no skeleton: said by the server as a refusal, shown as nothing here. */
async function load(target: string): Promise<Found | null> {
  try {
    return await api.skeleton(target);
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) return null;
    throw error;
  }
}

export function AppSkeleton({ target }: { target: string }) {
  const [state] = useLoad(`skeleton:${target}`, () => load(target));
  if (state.status === "loading") return <Placeholder className="h-32 rounded-xl" />;
  if (state.status !== "ready") {
    return (
      <p className="m-0 text-[13px] text-ink-3">Couldn't read the app's code for its skeleton.</p>
    );
  }
  const found = ready(state);
  if (!found) return null;
  const when = windowWords(found.window);
  const groups = GROUPS.map((group) => ({
    ...group,
    entries: ranked(found.entries.filter((entry) => entry.kind === group.kind)),
  })).filter((group) => group.entries.length);
  const aside = found.window
    ? `From the code, with use from ${count(found.window.sessions, "conversation")} ${when}`
    : "From the code";
  return (
    <section aria-label="Skeleton">
      <GroupHeading title="Skeleton" aside={aside} />
      <Group>
        {groups.length ? null : (
          <p className="m-0 px-[18px] py-3 text-[14px] text-ink-2">
            No prompts, tools, model calls, routes or stores found in the code yet.
          </p>
        )}
        {groups.map((group) => (
          <Kind key={group.kind} heading={group.title} entries={group.entries} when={when} />
        ))}
        {found.unseen.length ? (
          <div className={SECTION}>
            <SectionHeading>Seen in use, not found in the code</SectionHeading>
            <ul aria-label="Seen in use, not found in the code" className="m-0 list-none p-0">
              {found.unseen.map((use) => (
                <li key={`${use.kind}:${use.name}`} className={ROW}>
                  <span className="min-w-0 truncate font-mono text-[13px]">{use.name}</span>
                  <span className="text-[13px] text-ink-2">{KIND_WORDS[use.kind] ?? use.kind}</span>
                  <span className="ms-auto shrink-0 text-[13px] text-ink-2 tabular-nums">
                    {use.calls ? `${count(use.calls, "call")} ${when}` : "offered, not called"}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </Group>
    </section>
  );
}

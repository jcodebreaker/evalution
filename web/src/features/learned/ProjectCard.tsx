import { useState } from "react";
import { api } from "../../lib/api";
import { cn } from "../../lib/cn";
import { seconds, usd } from "../../lib/format";
import type { Understanding } from "../../lib/schema";
import { MoreMenu } from "../../ui/menu";
import { Group } from "../shared/group";

export type Card = Understanding["card"];

/** Puts `text` on the clipboard, through a hidden field where the clipboard API is missing. */
async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const field = document.createElement("textarea");
    field.value = text;
    field.setAttribute("readonly", "");
    field.style.position = "fixed";
    field.style.opacity = "0";
    document.body.append(field);
    field.select();
    const done = document.execCommand("copy");
    field.remove();
    return done;
  }
}

function Figure({ value, label, sub }: { value: string; label: string; sub?: string }) {
  return (
    <div className="min-w-0 border-separator px-4 py-3 not-first:sm:border-s max-sm:[&:nth-child(n+3)]:border-t max-sm:[&:nth-child(even)]:border-s">
      <b className="block text-[20px] font-bold tracking-[-0.4px] tabular-nums">{value}</b>
      <small className="block text-[12px] text-ink-2">{label}</small>
      {sub ? (
        <i className="mt-0.5 block truncate text-[11.5px] text-ink-3 not-italic">{sub}</i>
      ) : null}
    </div>
  );
}

/** The app at a glance: what it is, what it's built with, its numbers, and whether it changed. */
export function ProjectCard({ card, target }: { card: Card; target: string }) {
  const { numbers } = card;
  const [copied, setCopied] = useState<string | null>(null);
  const status =
    card.status === "learning"
      ? { label: "Learning", tone: "bg-bubble text-ink-2" }
      : card.status === "out_of_date"
        ? {
            label: card.changes
              ? `Changed since learned · ${card.changes}`
              : "Changed since learned",
            tone: "bg-warning/10 text-warning",
          }
        : null;
  const tests =
    numbers.tests_passing === null
      ? numbers.tests
        ? "not run yet"
        : undefined
      : `${numbers.tests_passing} pass · ${numbers.tests - numbers.tests_passing} fail`;
  const copyCard = async () => {
    let done = false;
    try {
      done = await copy(await api.cardMarkdown(target));
    } catch {
      done = false;
    }
    setCopied(done ? "Copied" : "Couldn't copy");
    window.setTimeout(() => setCopied(null), 1800);
  };
  return (
    <Group className="px-[18px] pt-[18px]">
      <div className="flex items-start gap-3.5">
        <div className="grid size-11 shrink-0 place-items-center rounded-xl bg-primary text-[19px] font-bold text-primary-foreground">
          {card.name.slice(0, 1).toUpperCase()}
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="m-0 truncate text-[18px] font-bold tracking-[-0.3px]">{card.name}</h2>
          {card.one_line ? (
            <p className="m-0 mt-0.5 max-w-[56ch] text-[13.5px] text-ink-2">{card.one_line}</p>
          ) : null}
          {status ? (
            <span
              className={cn(
                "mt-2 inline-flex rounded-full px-2.5 py-0.5 text-[12px] font-medium",
                status.tone,
              )}
            >
              {status.label}
            </span>
          ) : null}
        </div>
        {copied ? <span className="pt-1 text-[12px] text-ink-3">{copied}</span> : null}
        <MoreMenu
          label="More"
          actions={[{ label: "Copy card", onClick: () => void copyCard() }]}
          className="-me-1.5"
        />
      </div>
      {card.stack.length ? (
        <div className="mt-3.5 mb-1 flex flex-wrap gap-1.5">
          {card.stack.map((entry) => (
            <span
              key={entry}
              className="rounded-[7px] bg-bubble px-2.5 py-0.5 text-[12px] text-ink-2"
            >
              {entry}
            </span>
          ))}
        </div>
      ) : null}
      <div className="-mx-[18px] mt-3 grid grid-cols-4 border-t border-separator max-sm:grid-cols-2">
        <Figure
          value={String(numbers.tools)}
          label="tools"
          sub={numbers.tools ? `${numbers.tools_seen} seen in use` : undefined}
        />
        <Figure
          value={String(numbers.state_kinds.length)}
          label="kinds of state"
          sub={numbers.state_kinds.join(" · ") || undefined}
        />
        <Figure
          value={numbers.ask_seconds === null ? "—" : seconds(numbers.ask_seconds)}
          label="typical ask"
          sub={numbers.ask_usd === null ? undefined : `${usd(numbers.ask_usd)} per ask`}
        />
        <Figure value={String(numbers.tests)} label="tests" sub={tests} />
      </div>
    </Group>
  );
}

/** An app in the picker: its name and what it is. */
export function CardRow({ name, card }: { name: string; card: Card | null }) {
  return (
    <span className="flex min-w-0 max-w-72 flex-col">
      <span className="truncate font-medium">{name}</span>
      {card?.one_line ? (
        <span className="truncate text-[11.5px] text-ink-3">{card.one_line}</span>
      ) : null}
    </span>
  );
}

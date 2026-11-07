import type { FormEvent, ReactNode } from "react";
import { useState } from "react";
import { cn } from "../../lib/cn";
import type { Claim } from "../../lib/schema";
import { Button } from "../../ui/button";
import { Input } from "../../ui/field";

export type Checked = Pick<
  Claim,
  "verdict" | "fix" | "evidence" | "updated_from" | "stale" | "flagged"
>;

export type Verb = { verdict: "right" | "wrong" } | { fix: string };

/** Right, Fix and Wrong for one claim or box, with the field a fix is typed in. */
export function Check({
  item,
  label,
  children,
  always,
  onSteer,
}: {
  item: Checked;
  /** What the actions are about, for their accessible names. */
  label: string;
  children: ReactNode;
  /** Actions at full strength (the box panel); else they brighten on hover. */
  always?: boolean;
  onSteer: (verb: Verb) => void;
}) {
  const [fixing, setFixing] = useState(false);
  const [text, setText] = useState(item.fix ?? "");
  const done = item.verdict !== undefined || item.fix !== undefined;
  const save = (event: FormEvent) => {
    event.preventDefault();
    const fix = text.trim();
    if (fix) onSteer({ fix });
    setFixing(false);
  };
  const action = (on: boolean, tone: string) =>
    cn(
      "h-[26px] rounded-[7px] px-2.5 text-[12px] focus-visible:outline-2 focus-visible:outline-ring",
      on ? tone : "bg-bubble text-ink-2 hover:text-foreground",
    );
  return (
    <div
      className={cn(
        "group/check relative grid items-start gap-3 max-sm:grid-cols-1 max-sm:gap-2",
        always || done ? "grid-cols-[minmax(0,1fr)_auto]" : "grid-cols-1",
      )}
    >
      <div className="min-w-0">{children}</div>
      <fieldset
        aria-label={label}
        className={cn(
          "m-0 flex min-w-0 gap-1 border-0 p-0 transition-opacity",
          // Unchecked, the actions stay out of the way: over the claim's corner on hover or focus.
          !always &&
            !done &&
            "pointer-events-none absolute end-0 top-0 rounded-[9px] bg-group p-0.5 opacity-0 shadow-sm group-focus-within/check:pointer-events-auto group-focus-within/check:opacity-100 group-hover/check:pointer-events-auto group-hover/check:opacity-100",
        )}
      >
        <button
          type="button"
          className={action(item.verdict === "right", "bg-success/10 text-success")}
          onClick={() => onSteer({ verdict: "right" })}
        >
          Right
        </button>
        <button
          type="button"
          className={action(fixing || item.fix !== undefined, "bg-selection-strong text-tint")}
          onClick={() => setFixing((now) => !now)}
        >
          Fix
        </button>
        <button
          type="button"
          className={action(item.verdict === "wrong", "bg-destructive/10 text-destructive")}
          onClick={() => onSteer({ verdict: "wrong" })}
        >
          Wrong
        </button>
      </fieldset>
      {fixing ? (
        <form onSubmit={save} className="col-span-full flex gap-2">
          <Input
            autoFocus
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder="What's right instead?"
            aria-label={`${label}: what's right instead`}
            className="bg-window"
          />
          <Button type="submit" disabled={!text.trim()}>
            Save
          </Button>
        </form>
      ) : null}
    </div>
  );
}

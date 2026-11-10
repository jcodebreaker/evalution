import type { FormEvent } from "react";
import { useState } from "react";
import { cn } from "../../lib/cn";
import type { Steer, Understanding } from "../../lib/schema";
import { Input } from "../../ui/field";

export function Priorities({
  priorities,
  onSteer,
}: {
  priorities: Understanding["priorities"];
  onSteer: (steer: Steer) => void;
}) {
  const [adding, setAdding] = useState(false);
  const [title, setTitle] = useState("");
  const add = (event: FormEvent) => {
    event.preventDefault();
    const text = title.trim();
    if (text) onSteer({ priority: { title: text } });
    setTitle("");
    setAdding(false);
  };
  const chip = "inline-flex h-7 items-center gap-1.5 rounded-full border px-3 text-sm";
  return (
    <div className="flex flex-wrap gap-1.5 px-[18px] pt-1 pb-2">
      {priorities.map((entry) => (
        <button
          key={entry.id}
          type="button"
          aria-pressed={entry.picked}
          onClick={() => onSteer({ priority: entry.id, picked: !entry.picked })}
          className={cn(
            chip,
            "focus-visible:outline-2 focus-visible:outline-tint",
            entry.picked
              ? "border-tint-soft-2 bg-tint-soft font-medium text-tint-ink"
              : "border-separator bg-group text-ink-2 hover:bg-sunk",
          )}
        >
          {entry.title}
        </button>
      ))}
      {adding ? (
        <form onSubmit={add} className="flex gap-1.5">
          <Input
            autoFocus
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            onBlur={() => !title.trim() && setAdding(false)}
            placeholder="What matters"
            aria-label="What matters most to test"
            className="h-7 w-56 rounded-full"
          />
        </form>
      ) : (
        <button
          type="button"
          onClick={() => setAdding(true)}
          className={cn(
            chip,
            "border-dashed border-rule-strong text-ink-2 hover:bg-sunk focus-visible:outline-2 focus-visible:outline-tint",
          )}
        >
          + Add your own
        </button>
      )}
    </div>
  );
}

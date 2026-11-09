import { Plus } from "lucide-react";
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
  const chip = "inline-flex h-[30px] items-center gap-1.5 rounded-full px-3 text-[14px]";
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
            "focus-visible:outline-2 focus-visible:outline-ring",
            entry.picked ? "bg-selection-strong font-medium text-tint" : "bg-bubble text-ink-2",
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
            className="h-[30px] w-56 rounded-full"
          />
        </form>
      ) : (
        <button
          type="button"
          onClick={() => setAdding(true)}
          className={cn(
            chip,
            "bg-bubble text-ink-2 focus-visible:outline-2 focus-visible:outline-ring",
          )}
        >
          <Plus className="size-3" aria-hidden />
          Add your own
        </button>
      )}
    </div>
  );
}

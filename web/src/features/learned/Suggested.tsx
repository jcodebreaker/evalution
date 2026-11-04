import { useState } from "react";
import { errorText } from "../../lib/api";
import { cn } from "../../lib/cn";
import { count, usd } from "../../lib/format";
import type { Steer, Understanding } from "../../lib/schema";
import { Button } from "../../ui/button";
import { Group } from "../shared/group";

export function Suggested({
  understanding,
  onSteer,
  onAdd,
}: {
  understanding: Understanding;
  onSteer: (steer: Steer) => void;
  onAdd: (ids: string[]) => Promise<void>;
}) {
  const { starters, priorities } = understanding;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const picked = starters.filter((entry) => entry.selected && !entry.exists);
  const priorityTitle = (id?: string) => priorities.find((entry) => entry.id === id)?.title;
  const add = async () => {
    setBusy(true);
    setError(null);
    try {
      await onAdd(picked.map((entry) => entry.id));
    } catch (failure) {
      setError(errorText(failure, "Couldn't reach the AI."));
    }
    setBusy(false);
  };
  return (
    <Group>
      <div className="border-b border-separator px-[18px] py-3.5">
        <h2 className="m-0 text-[15px] font-semibold">Suggested tests</h2>
      </div>
      {starters.map((entry) => {
        const why = [priorityTitle(entry.priority), entry.why].filter(Boolean).join(" · ");
        return (
          <label
            key={entry.id}
            className={cn(
              "grid grid-cols-[22px_minmax(0,1fr)_auto] items-start gap-3 border-separator px-[18px] py-[11px] not-first:border-t",
              entry.exists && "text-ink-2",
            )}
          >
            <input
              type="checkbox"
              checked={entry.exists || entry.selected}
              disabled={entry.exists}
              onChange={(event) => onSteer({ starter: entry.id, selected: event.target.checked })}
              className="mt-[3px] size-4 accent-primary"
            />
            <div className="min-w-0">
              <p className="m-0 font-medium text-[14px]">{entry.title}</p>
              {entry.what ? (
                <small className="mt-0.5 block text-[12.5px] text-ink-2">{entry.what}</small>
              ) : null}
              {why && !entry.exists ? (
                <div className="mt-[3px] text-[11.5px] text-ink-3">{why}</div>
              ) : null}
            </div>
            <span className="text-[12.5px] whitespace-nowrap text-ink-3 tabular-nums">
              {entry.exists ? "Added" : `about ${usd(entry.usd_per_try)} a try`}
            </span>
          </label>
        );
      })}
      {starters.some((entry) => !entry.exists) ? (
        <div className="flex flex-wrap items-center justify-end gap-2.5 border-t border-separator px-[18px] py-3">
          {error ? <span className="me-auto text-[12px] text-destructive">{error}</span> : null}
          <Button disabled={!picked.length || busy} onClick={() => void add()}>
            {picked.length ? `Add ${count(picked.length, "test")}` : "Pick a test"}
          </Button>
        </div>
      ) : null}
    </Group>
  );
}

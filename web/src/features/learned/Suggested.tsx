import { Sparkles } from "lucide-react";
import { useState } from "react";
import { errorText } from "../../lib/api";
import { count } from "../../lib/format";
import type { Steer, Understanding } from "../../lib/schema";
import { Button } from "../../ui/button";
import { Card, Chip, Switch } from "../connect/kit";

/** The tests the test writer suggests for this app, each picked with a switch; the picked ones
 * are planned together. One already a test says so. */
export function Suggested({
  understanding,
  onSteer,
  onAdd,
}: {
  understanding: Pick<Understanding, "starters" | "priorities">;
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
    <Card aria-label="Suggested tests">
      <ul className="m-0 list-none p-0">
        {starters.map((entry) => {
          const why = [priorityTitle(entry.priority), entry.why].filter(Boolean).join(" · ");
          return (
            <li
              key={entry.id}
              className="flex items-center gap-3.5 border-rule px-5 py-3.5 not-last:border-b max-md:px-4"
            >
              <Switch
                on={entry.exists || entry.selected}
                label={entry.title}
                disabled={entry.exists}
                onChange={(on) => onSteer({ starter: entry.id, selected: on })}
              />
              <span className="min-w-0 flex-1">
                <b className="block font-medium">{entry.title}</b>
                <small className="block text-sm text-ink-2">
                  {entry.exists ? "Already a test" : entry.what}
                </small>
                {why && !entry.exists ? (
                  <small className="block text-sm text-ink-3">{why}</small>
                ) : null}
              </span>
              {entry.exists ? <Chip tone="ok">Added</Chip> : null}
            </li>
          );
        })}
      </ul>
      {starters.some((entry) => !entry.exists) ? (
        <div className="flex flex-wrap items-center justify-end gap-2.5 border-t border-rule px-5 py-3.5 max-md:px-4">
          {error ? <span className="me-auto text-sm text-fail">{error}</span> : null}
          <Button size="lg" disabled={!picked.length || busy} onClick={() => void add()}>
            <Sparkles />
            {picked.length ? `Add ${count(picked.length, "test")}` : "Pick a test"}
          </Button>
        </div>
      ) : null}
    </Card>
  );
}

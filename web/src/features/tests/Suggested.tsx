import { Plus, Sparkles } from "lucide-react";
import { useState } from "react";
import { api } from "../../lib/api";
import type { Understanding } from "../../lib/schema";
import { ready, useLoad } from "../../lib/use-load";
import { Button } from "../../ui/button";
import { IconBox, Panel, PanelHead } from "../shared/kit";

type Starter = Understanding["starters"][number];

/** What the test writer is asked, to write the suggested tests. */
export function suggestedAsk(starters: readonly Starter[]): string {
  if (starters.length === 1) {
    const [one] = starters;
    return `Write a test: ${one?.title}.${one?.what ? ` ${one.what}` : ""}`;
  }
  return [
    "Write these suggested tests:",
    ...starters.map((entry) => `- ${entry.title}${entry.what ? `: ${entry.what}` : ""}`),
  ].join("\n");
}

/**
 * The tests suggested from what was learned of the app and not written yet, three at most, each
 * handed to the test writer by Add. Nothing when there are none.
 */
export function Suggested({
  target,
  onAsk,
}: {
  target: string;
  onAsk: (text: string) => Promise<void>;
}) {
  const [load] = useLoad(`suggested:${target}`, () => api.understanding(target));
  const [busy, setBusy] = useState(false);
  const open = (ready(load)?.starters ?? []).filter((entry) => !entry.exists).slice(0, 3);
  if (!open.length) return null;
  const add = async (starters: Starter[]) => {
    setBusy(true);
    try {
      await onAsk(suggestedAsk(starters));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Panel aria-label="Suggested tests">
      <PanelHead>
        <IconBox tone="tint" small>
          <Sparkles />
        </IconBox>
        <h2 className="m-0 text-[15px] font-semibold">Suggested from what it learned</h2>
        <span className="flex-1" />
        {open.length > 1 ? (
          <Button variant="secondary" disabled={busy} onClick={() => void add(open)}>
            {`Add all ${open.length}`}
          </Button>
        ) : null}
      </PanelHead>
      <div className="grid grid-cols-3 gap-5 p-5 max-lg:grid-cols-1">
        {open.map((entry) => (
          <div
            key={entry.id}
            className="flex min-w-0 flex-col gap-2 rounded-2xl border border-rule p-5"
          >
            <b className="font-medium">{entry.title}</b>
            {entry.what || entry.why ? (
              <span className="text-sm text-ink-3">{entry.what || entry.why}</span>
            ) : null}
            <Button
              variant="secondary"
              className="mt-1 self-start"
              disabled={busy}
              aria-label={`Add ${entry.title}`}
              onClick={() => void add([entry])}
            >
              <Plus />
              Add
            </Button>
          </div>
        ))}
      </div>
    </Panel>
  );
}

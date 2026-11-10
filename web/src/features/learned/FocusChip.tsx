import { useState } from "react";
import { ApiError, focusApi } from "../../lib/api";
import type { AgentChoices } from "../../lib/schema";
import { ready, useLoad } from "../../lib/use-load";
import { Button } from "../../ui/button";
import { ALL, FocusPick } from "../connect/FocusPick";
import { Chip } from "../connect/kit";

/** No code folder is no agents: said by the server as a refusal, shown as nothing here. */
async function loadAgents(target: string): Promise<AgentChoices | null> {
  try {
    return await focusApi.agents(target);
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) return null;
    throw error;
  }
}

/**
 * "Testing: planner" on a platform that hosts many agents: the one the learn starts from
 * (`code.focus`), with a way to pick another, or all of them, from the same list connecting
 * offered. Nothing on an app with few agents and no focus. `onChange` runs once it is saved.
 */
export function FocusChip({ target, onChange }: { target: string; onChange?: () => void }) {
  const [state, reload] = useLoad(`agents:${target}`, () => loadAgents(target));
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const found = ready(state);
  if (!found || (!found.focus && !found.asks)) return null;
  const named = found.choices.find((choice) => choice.value === found.focus);
  const words = found.focus ? (named?.label ?? found.focus) : "all agents";

  async function pick(value: string) {
    setSaving(true);
    setError(null);
    try {
      await focusApi.setFocus(target, value === ALL ? null : value);
      await reload();
      setOpen(false);
      onChange?.();
    } catch (failed) {
      setError(failed instanceof Error ? failed.message : "Couldn't save the agent to test.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <Chip tone={found.focus ? "tint" : "plain"}>Testing: {words}</Chip>
        <Button
          variant="link"
          size="sm"
          aria-expanded={open}
          onClick={() => setOpen((was) => !was)}
        >
          {open ? "Keep it" : "Change"}
        </Button>
      </div>
      {open ? (
        <FocusPick
          label="Which agent do you want to test?"
          choices={found.choices}
          value={found.focus ?? ALL}
          disabled={saving}
          onPick={(value) => void pick(value)}
        />
      ) : null}
      {error ? (
        <p role="alert" className="m-0 text-sm text-fail">
          {error}
        </p>
      ) : null}
    </div>
  );
}

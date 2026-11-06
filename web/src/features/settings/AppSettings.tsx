import { useState } from "react";
import { api, errorText } from "../../lib/api";
import { cn } from "../../lib/cn";
import type { Hooks, Knowledge } from "../../lib/schema";
import { useLoad } from "../../lib/use-load";
import { useAccount } from "../account/account";
import { Group } from "../shared/group";
import { Row } from "../shared/Row";

const UPDATE_MODES: { value: Knowledge["mode"]; label: string }[] = [
  { value: "auto", label: "Automatically" },
  { value: "manual", label: "When I ask" },
];

/** Settings for the open app: when what it knows is updated, and the hooks in its repo. */
export function AppSettings({ target }: { target: string }) {
  // Hosted, the server has no repo to put hooks in.
  const hosted = useAccount() !== null;
  const [updates, reloadUpdates] = useLoad(`updates:${target}`, () => api.updates.get(target));
  const [hooks, reloadHooks] = useLoad(hosted ? null : `hooks:${target}`, () =>
    api.hooks.get(target),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mode = updates.status === "ready" ? updates.data.mode : null;
  const found: Hooks | null = hooks.status === "ready" ? hooks.data : null;
  const act = async (work: () => Promise<unknown>, reload: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await work();
      await reload();
    } catch (failure) {
      setError(errorText(failure, "Couldn't save."));
    }
    setBusy(false);
  };
  const left = found
    ? Object.entries(found.hooks).filter(([, state]) => state !== "on" && state !== "off")
    : [];
  return (
    <div className="flex flex-col gap-2">
      <h2 className="m-0 px-1 text-[13px] font-semibold">{target}</h2>
      <Group>
        <Row label="Update what it knows" group>
          <span className="flex flex-wrap gap-0.5 self-start rounded-lg bg-selection p-0.5">
            {UPDATE_MODES.map((entry) => (
              <button
                key={entry.value}
                type="button"
                disabled={busy || mode === null}
                aria-pressed={mode === entry.value}
                onClick={() => void act(() => api.updates.set(target, entry.value), reloadUpdates)}
                className={cn(
                  "h-7 rounded-md px-2.5 text-[12.5px] focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-50",
                  mode === entry.value
                    ? "bg-background font-medium shadow-sm"
                    : "text-ink-2 hover:text-foreground",
                )}
              >
                {entry.label}
              </button>
            ))}
          </span>
        </Row>
        {hosted ? null : (
          <Row label="Git hooks">
            {found && !found.available ? (
              <span className="pt-1.5 text-[13px] text-ink-2">Not available: {found.reason}</span>
            ) : (
              <input
                type="checkbox"
                className="mt-2 size-4 self-start accent-primary"
                disabled={busy || !found}
                checked={found?.installed ?? false}
                onChange={(event) =>
                  void act(
                    () =>
                      event.target.checked ? api.hooks.install(target) : api.hooks.remove(target),
                    reloadHooks,
                  )
                }
              />
            )}
            {left.map(([hook, why]) => (
              <span key={hook} className="text-[12px] text-ink-3">
                {hook} {why}
              </span>
            ))}
          </Row>
        )}
      </Group>
      {error ? <p className="m-0 px-1 text-[12.5px] text-destructive">{error}</p> : null}
    </div>
  );
}

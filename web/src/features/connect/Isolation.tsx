import { useState } from "react";
import { api, errorText } from "../../lib/api";
import type { Target } from "../../lib/schema";
import { Group } from "../shared/group";

const KINDS = ["conversation", "memory", "files"] as const;

/** What every new test must start clean of, out of what the app promises. By default nothing. */
export function Isolation({
  target,
  onChange,
}: {
  target: Target;
  onChange: () => Promise<unknown>;
}) {
  const [error, setError] = useState<string | null>(null);
  const toggle = async (kind: string, on: boolean) => {
    const required = on
      ? [...target.required_clean, kind]
      : target.required_clean.filter((entry) => entry !== kind);
    setError(null);
    try {
      await api.setIsolation(target.name, required);
      await onChange();
    } catch (failure) {
      setError(errorText(failure, "Couldn't save it."));
    }
  };
  return (
    <div className="flex flex-col gap-2 text-[13px]">
      {target.isolation_note ? <p className="m-0 text-ink-2">{target.isolation_note}</p> : null}
      <Group>
        {KINDS.map((kind) => {
          const promised = target.starts_clean.includes(kind);
          return (
            <label
              key={kind}
              className="flex items-center gap-2.5 border-separator px-3.5 py-2.5 not-first:border-t"
            >
              <input
                type="checkbox"
                disabled={!promised}
                checked={target.required_clean.includes(kind)}
                onChange={(event) => void toggle(kind, event.target.checked)}
              />
              <span className={promised ? undefined : "text-ink-3"}>
                Every test starts with no earlier {kind}
              </span>
              {promised ? null : (
                <span className="ms-auto text-[12px] text-ink-3">Not promised</span>
              )}
            </label>
          );
        })}
      </Group>
      {error ? <p className="m-0 text-[12.5px] text-destructive">{error}</p> : null}
    </div>
  );
}

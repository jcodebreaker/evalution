import type { ModelTest } from "../../lib/schema";
import { StatusDot } from "../../ui/status";
import { Group } from "../shared/group";

const PARTS = [
  ["strong", "Thinking"],
  ["light", "Filler"],
] as const;

export function TestResult({ test }: { test: ModelTest }) {
  return (
    <Group>
      {PARTS.map(([key, label]) => {
        const part = test[key];
        return (
          <div
            key={key}
            className="flex items-start gap-3 border-separator px-4 py-2.5 text-[14px] not-first:border-t"
          >
            <StatusDot
              state={part.ok ? "pass" : "fail"}
              label={part.ok ? "OK" : "Problem"}
              className="mt-[7px]"
            />
            <span className="w-16 shrink-0 font-medium">{label}</span>
            <span className="min-w-0 flex-1 break-words text-ink-2">{part.detail}</span>
            <span className="shrink-0 text-[12.5px] text-ink-3 tabular-nums">
              {(part.ms / 1000).toFixed(1)} s
            </span>
          </div>
        );
      })}
    </Group>
  );
}

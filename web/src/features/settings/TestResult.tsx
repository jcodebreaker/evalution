import { Check, X } from "lucide-react";
import type { ModelTest } from "../../lib/schema";
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
            className="flex items-start gap-2.5 border-separator px-3.5 py-2.5 text-[13px] not-first:border-t"
          >
            {part.ok ? (
              <Check className="mt-0.5 size-3.5 shrink-0 text-success" aria-label="OK" />
            ) : (
              <X className="mt-0.5 size-3.5 shrink-0 text-destructive" aria-label="Problem" />
            )}
            <span className="w-16 shrink-0 font-medium">{label}</span>
            <span className="min-w-0 flex-1 break-words text-ink-2">{part.detail}</span>
            <span className="shrink-0 text-[12px] text-ink-3 tabular-nums">
              {(part.ms / 1000).toFixed(1)} s
            </span>
          </div>
        );
      })}
    </Group>
  );
}

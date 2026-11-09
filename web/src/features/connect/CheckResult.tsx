import { Check, X } from "lucide-react";
import type { TargetCheck } from "../../lib/schema";
import { Group } from "../shared/group";

const PARTS = [
  ["adapter", "App"],
  ["phoenix", "Traces"],
  ["code", "Code"],
] as const;

export function CheckResult({ check }: { check: TargetCheck }) {
  return (
    <Group>
      {PARTS.map(([key, label]) => {
        const part = check[key];
        return (
          <div
            key={key}
            className="flex items-start gap-2.5 border-separator px-3.5 py-2.5 text-[14px] not-first:border-t"
          >
            {part.ok ? (
              <Check className="mt-0.5 size-3.5 shrink-0 text-success" aria-label="OK" />
            ) : (
              <X className="mt-0.5 size-3.5 shrink-0 text-destructive" aria-label="Problem" />
            )}
            <span className="w-16 shrink-0 font-medium">{label}</span>
            <span className="min-w-0 text-ink-2">{part.detail}</span>
          </div>
        );
      })}
    </Group>
  );
}

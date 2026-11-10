import type { TargetCheck } from "../../lib/schema";
import { StatusDot } from "../../ui/status";
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
            className="flex items-start gap-3 border-separator px-4 py-2.5 text-sm not-first:border-t"
          >
            <StatusDot
              state={part.ok ? "pass" : "fail"}
              label={part.ok ? "OK" : "Problem"}
              className="mt-[7px]"
            />
            <span className="w-16 shrink-0 font-medium">{label}</span>
            <span className="min-w-0 text-ink-2">{part.detail}</span>
          </div>
        );
      })}
    </Group>
  );
}

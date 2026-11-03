import { cn } from "../../lib/cn";
import type { RunDetail } from "../../lib/schema";
import { Group } from "../shared/group";
import { VERDICT_CELL } from "../shared/marks";

/** The last ten runs, one column each; a line marks where the prompt changed. */
export function History({
  run,
  label,
  onTest,
}: {
  run: RunDetail;
  label: (run: string) => string;
  onTest: (id: string) => void;
}) {
  const changed = (index: number) =>
    index > 0 && run.history[index]?.prompt !== run.history[index - 1]?.prompt;
  return (
    <Group className="overflow-x-auto">
      <table className="w-full min-w-[520px] border-collapse">
        <thead>
          <tr className="text-[11px] text-ink-3">
            <th />
            {run.history.map((entry, index) => (
              <th
                key={entry.run}
                title={entry.prompt}
                className={cn(
                  "px-0 pt-2.5 pb-1.5 text-center font-medium",
                  changed(index) && "border-s border-ink-3",
                )}
              >
                {label(entry.run).replace(/^Run /, "")}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {run.cases.map((row) => (
            <tr key={row.case} onClick={() => onTest(row.case)} className="cursor-default">
              <td className="border-t border-separator px-3.5 py-[7px] whitespace-nowrap">
                {row.name}
              </td>
              {run.history.map((entry, index) => {
                const verdict = entry.verdicts[row.case] ?? null;
                return (
                  <td
                    key={entry.run}
                    className={cn(
                      "border-t border-separator py-1.5 text-center",
                      changed(index) && "border-s border-s-ink-3",
                    )}
                  >
                    <span
                      className={cn(
                        "inline-block size-3 rounded-[3px] align-middle",
                        verdict ? VERDICT_CELL[verdict] : "ring-1 ring-inset ring-separator",
                      )}
                    />
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </Group>
  );
}

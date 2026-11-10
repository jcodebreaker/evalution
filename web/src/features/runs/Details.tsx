import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import { seconds, tokens } from "../../lib/format";
import type { RunDetail, RunSummary } from "../../lib/schema";
import { Picker } from "../../ui/menu";
import { Group } from "../shared/group";
import { ScoreBar } from "../shared/marks";
import { History } from "./History";
import { Metrics } from "./Metrics";

/** Folded under the results: the base run, the numbers against it, each test's scores, and
 * history. */
export function Scores({
  run,
  runs,
  label,
  onBase,
  onTest,
}: {
  run: RunDetail;
  runs: RunSummary[];
  label: (run: string) => string;
  onBase: (run: string) => void;
  onTest: (id: string) => void;
}) {
  const others = runs
    .filter(
      (entry) =>
        entry.run !== run.run &&
        entry.target === run.target &&
        entry.set === run.set &&
        entry.started_at < run.started_at,
    )
    .sort((a, b) => b.started_at - a.started_at);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2 px-1 text-sm text-ink-3">
        {run.prompt ? <span>prompt {run.prompt}</span> : null}
        {others.length ? (
          <>
            {run.prompt ? <span>·</span> : null}
            <span>Compared with</span>
            <Picker
              label="Compare with"
              value={run.base ? label(run.base.run) : "—"}
              options={others.map((entry) => ({ value: entry.run, label: label(entry.run) }))}
              onPick={onBase}
              className="h-6 bg-sunk-2 font-medium text-foreground"
            />
          </>
        ) : null}
      </div>
      <Metrics run={run} />
      <Group className="overflow-x-auto">
        <table className="w-full min-w-[620px] border-collapse">
          <thead>
            <tr className="text-start text-xs font-medium text-ink-3">
              <Th>Test</Th>
              <Th className="w-[150px]">Steps score</Th>
              <Th className="w-[150px]">Reply score</Th>
              <Th end>Time</Th>
              <Th end>Tokens</Th>
            </tr>
          </thead>
          <tbody>
            {run.cases.map((row) => (
              <tr key={row.case} className="whitespace-nowrap">
                <Td>{row.name}</Td>
                <Td>
                  <ScoreBar value={row.tools} pass={run.pass_marks.tools} />
                </Td>
                <Td>
                  <ScoreBar value={row.reply} pass={run.pass_marks.reply} />
                </Td>
                <Td end>{row.seconds === null ? "—" : seconds(row.seconds)}</Td>
                <Td end>{row.tokens === null ? "—" : tokens(row.tokens)}</Td>
              </tr>
            ))}
          </tbody>
        </table>
      </Group>
      {run.history.length > 1 ? <History run={run} label={label} onTest={onTest} /> : null}
    </div>
  );
}

function Th({
  children,
  end,
  className,
}: {
  children: ReactNode;
  end?: boolean;
  className?: string;
}) {
  return (
    <th
      className={cn("px-3.5 pt-2.5 pb-2 font-medium", end ? "text-end" : "text-start", className)}
    >
      {children}
    </th>
  );
}

function Td({ children, end }: { children: ReactNode; end?: boolean }) {
  return (
    <td
      className={cn(
        "border-t border-separator px-3.5 py-2.5",
        end && "text-end tabular-nums text-ink-2",
      )}
    >
      {children}
    </td>
  );
}

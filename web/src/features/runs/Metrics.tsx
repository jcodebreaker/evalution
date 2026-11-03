import type { ReactNode } from "react";
import { percent, score, seconds, signed, usd } from "../../lib/format";
import type { RunDetail } from "../../lib/schema";
import { Group } from "../shared/group";

/** Passed, steps score, reply score, median time and what was spent, each with its change from the base run. */
export function Metrics({ run }: { run: RunDetail }) {
  const { totals, delta } = run;
  const tone = (good: boolean, text: string) => (
    <span className={good ? "text-success" : "text-destructive"}>{text}</span>
  );
  const change = (value: number, text: string, lowerIsBetter = false) =>
    Math.abs(value) < 0.005 || text.startsWith("±")
      ? text
      : tone(lowerIsBetter ? value < 0 : value > 0, text);
  return (
    <Group className="grid grid-cols-5 max-[900px]:grid-cols-2">
      <Metric
        label="Passed"
        value={`${totals.passed} of ${totals.cases}`}
        delta={
          delta ? (
            <>
              {delta.better ? tone(true, `${delta.better} better`) : <span>0 better</span>}
              {delta.worse ? tone(false, `${delta.worse} worse`) : <span>0 worse</span>}
              {delta.changed ? <span>{`${delta.changed} test or rules changed`}</span> : null}
            </>
          ) : null
        }
      />
      <Metric
        label="Steps score"
        value={totals.tools === null ? "—" : score(totals.tools)}
        delta={delta ? change(delta.tools, signed(delta.tools)) : null}
      />
      <Metric
        label="Reply score"
        value={totals.reply === null ? "—" : score(totals.reply)}
        delta={delta ? change(delta.reply, signed(delta.reply)) : null}
      />
      <Metric
        label="Median time"
        value={seconds(totals.median_seconds)}
        delta={
          delta ? change(delta.median_seconds_pct, percent(delta.median_seconds_pct), true) : null
        }
      />
      <Metric
        label="Spent"
        value={totals.usd ? usd(totals.usd) : "—"}
        delta={delta ? change(delta.usd_pct, percent(delta.usd_pct), true) : null}
      />
    </Group>
  );
}

function Metric({ label, value, delta }: { label: string; value: string; delta: ReactNode }) {
  return (
    <div className="min-w-0 border-separator px-3.5 py-3 not-first:border-s max-[900px]:border-s-0 max-[900px]:not-first:border-t">
      <small className="block text-[11.5px] text-ink-2">{label}</small>
      <b className="mt-0.5 block text-[20px] font-semibold tracking-[-0.4px] tabular-nums">
        {value}
      </b>
      {delta ? <div className="mt-0.5 flex gap-2 text-[11.5px] text-ink-3">{delta}</div> : null}
    </div>
  );
}

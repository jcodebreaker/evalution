import { cn } from "../../lib/cn";
import { seconds, tokens } from "../../lib/format";
import type { ContextCall, TrajectoryStep } from "../../lib/schema";
import { Fold } from "../shared/group";

/** One ink shade per section, darkest first, so a bar reads the same in every call. */
const SHADES = [
  "bg-foreground/85",
  "bg-foreground/65",
  "bg-foreground/50",
  "bg-foreground/40",
  "bg-foreground/30",
  "bg-foreground/22",
  "bg-foreground/15",
  "bg-foreground/10",
  "bg-foreground/6",
];

const LABEL: Record<string, string> = {
  system: "System prompt",
  memory_profile: "Memory profile",
  memory: "Memory",
  context_brief: "Context brief",
  checkpoint: "Checkpoint",
  kept_tail: "Kept tail",
  tools: "Tools",
  turns: "Turns",
  notice: "Notice",
  other: "Other",
};

const label = (name: string) => LABEL[name] ?? name.replace(/_/g, " ");

/**
 * What the model saw on each call of a try, by section in tokens, and what happened step by
 * step. Hidden when the try captured neither.
 */
export function TryContext({
  context,
  trajectory,
}: {
  context: readonly ContextCall[];
  trajectory: readonly TrajectoryStep[];
}) {
  if (!context.length && !trajectory.length) return null;
  // Section names in the order they first appear, so each keeps its shade across calls.
  const names = [...new Set(context.flatMap((call) => call.sections.map((s) => s.name)))];
  const shade = (name: string) => SHADES[Math.min(names.indexOf(name), SHADES.length - 1)];
  return (
    <Fold title="What the model saw">
      <div className="flex flex-col gap-4">
        {context.map((call) => (
          <CallBar key={call.n} call={call} shade={shade} />
        ))}
        {trajectory.length ? <Steps steps={trajectory} /> : null}
      </div>
    </Fold>
  );
}

/** One model call: its sections as one stacked bar, then each with its tokens. */
function CallBar({
  call,
  shade,
}: {
  call: ContextCall;
  shade: (name: string) => string | undefined;
}) {
  const sum = call.sections.reduce((total, section) => total + section.tokens, 0) || 1;
  return (
    <div className="flex flex-col gap-1.5 text-[12.5px]">
      <div className="flex min-w-0 items-baseline gap-2">
        <span className="shrink-0 font-medium">Call {call.n}</span>
        <span className="min-w-0 flex-1 truncate font-mono text-[11.5px] text-ink-3">
          {call.model}
        </span>
        <span className="shrink-0 text-ink-3 tabular-nums">{tokens(call.tokens_in)} tokens</span>
      </div>
      <div className="flex h-2.5 gap-px overflow-hidden rounded-[3px] bg-selection">
        {call.sections.map((section, index) => (
          <span
            key={index}
            title={`${label(section.name)} · ${tokens(section.tokens)}`}
            className={cn("h-full min-w-px", shade(section.name))}
            style={{ width: `${(section.tokens / sum) * 100}%` }}
          />
        ))}
      </div>
      <ul className="m-0 flex list-none flex-col p-0">
        {call.sections.map((section, index) => {
          const line = (
            <>
              <i className={cn("size-2 shrink-0 rounded-[2px]", shade(section.name))} />
              <span className="min-w-0 flex-1 truncate">{label(section.name)}</span>
              <span className="text-ink-3 tabular-nums">{tokens(section.tokens)}</span>
            </>
          );
          return (
            <li key={index}>
              {section.preview ? (
                <details>
                  <summary className="flex cursor-default list-none items-center gap-2 rounded-md py-0.5 hover:bg-selection focus-visible:outline-2 focus-visible:outline-ring [&::-webkit-details-marker]:hidden">
                    {line}
                  </summary>
                  <pre className="m-0 mt-1 mb-1.5 rounded-[7px] bg-window px-2.5 py-2 font-mono text-[11.5px] leading-[1.55] break-words whitespace-pre-wrap text-ink-2">
                    {section.preview}
                  </pre>
                </details>
              ) : (
                <div className="flex items-center gap-2 py-0.5">{line}</div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** The try's trajectory, one compact line per step. */
function Steps({ steps }: { steps: readonly TrajectoryStep[] }) {
  return (
    <div className="flex flex-col gap-1">
      <h5 className="m-0 text-[11px] font-semibold text-ink-3">Steps</h5>
      <ol className="m-0 flex list-none flex-col p-0 text-[12.5px]">
        {steps.map((step, index) => (
          <li
            key={index}
            className="grid grid-cols-[44px_minmax(0,140px)_minmax(0,1fr)] items-baseline gap-2 py-0.5 max-md:grid-cols-[40px_minmax(0,1fr)]"
          >
            <span className="text-end text-ink-3 tabular-nums">{seconds(step.t, true)}</span>
            <span className="truncate text-ink-2">
              {step.actor} <span className="text-ink-3">{step.kind}</span>
              {step.automatic ? <span className="text-ink-3"> · automatic</span> : null}
            </span>
            <span className="line-clamp-2 break-words max-md:col-start-2">{step.text}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

import { cn } from "../../lib/cn";

const ABOUT_RUN = /^About benchmark run \S+, reading its traces: /;

export function Bubble({ text, waiting }: { text: string; waiting?: boolean }) {
  const asked = text.replace(ABOUT_RUN, "");
  return (
    <div
      className={cn(
        "flex max-w-[88%] flex-col gap-0.5 self-end rounded-2xl rounded-br-md bg-selection-strong px-3.5 py-2 text-[13.5px] leading-[1.45] whitespace-pre-wrap [overflow-wrap:anywhere]",
        waiting && "opacity-60",
      )}
    >
      {asked === text ? null : <span className="text-[11.5px] text-ink-2">About this run</span>}
      {asked}
    </div>
  );
}

import { cn } from "../../lib/cn";
import { askedText } from "./context";

export function Bubble({ text, waiting }: { text: string; waiting?: boolean }) {
  const { asked, about } = askedText(text);
  return (
    <div
      className={cn(
        "flex max-w-[88%] flex-col gap-0.5 self-end rounded-2xl rounded-br-md bg-tint-soft px-3.5 py-2 text-[13.5px] leading-[1.45] whitespace-pre-wrap [overflow-wrap:anywhere]",
        waiting && "opacity-60",
      )}
    >
      {about ? <span className="truncate text-[11.5px] text-ink-2">About {about}</span> : null}
      {asked}
    </div>
  );
}

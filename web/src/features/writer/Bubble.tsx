import { cn } from "../../lib/cn";

export function Bubble({ text, waiting }: { text: string; waiting?: boolean }) {
  return (
    <div
      className={cn(
        "max-w-[85%] self-end rounded-[18px] bg-bubble px-3.5 py-2 text-[14px] leading-[1.45] break-words whitespace-pre-wrap",
        waiting && "opacity-60",
      )}
    >
      {text}
    </div>
  );
}

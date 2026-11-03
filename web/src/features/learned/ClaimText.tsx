import { cn } from "../../lib/cn";
import type { Checked } from "./Check";
import { CodeMark, EvidenceLine } from "./evidence";

/** A claim's words: the admin's fix in its place, struck through when marked wrong. */
export function ClaimText({ text, item }: { text: string; item: Checked }) {
  return (
    <>
      <p
        className={cn(
          "m-0 text-[14px]",
          item.verdict === "wrong" && item.fix === undefined && "text-ink-3 line-through",
        )}
      >
        {item.fix ?? text}
      </p>
      <EvidenceLine
        evidence={item.fix !== undefined ? [{ kind: "admin", ref: "corrected" }] : item.evidence}
      />
      <CodeMark item={item} />
    </>
  );
}

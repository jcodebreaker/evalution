import { cn } from "../../lib/cn";
import type { Claim } from "../../lib/schema";
import { CodeMark, EvidenceLine } from "./evidence";

/** What a claim or box shows besides its words: a past fix or verdict, its evidence. */
export type Checked = Pick<
  Claim,
  "verdict" | "fix" | "evidence" | "updated_from" | "stale" | "flagged"
>;

/** A claim's words: the admin's fix in its place, struck through when marked wrong. */
export function ClaimText({ text, item }: { text: string; item: Checked }) {
  return (
    <>
      <p
        className={cn(
          "m-0 text-sm",
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

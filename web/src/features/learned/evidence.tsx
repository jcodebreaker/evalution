import type { Evidence } from "../../lib/schema";
import type { Checked } from "./ClaimText";

export const EVIDENCE_LABEL: Record<Evidence["kind"], string> = {
  prompt: "prompt",
  code: "code",
  traces: "traces",
  docs: "docs",
  admin: "you",
};

/** Where the code has moved under a claim or box, and the commit it was last updated from. */
export function CodeMark({ item }: { item: Checked }) {
  const out = item.flagged || item.stale === "direct";
  const near = !out && item.stale === "indirect";
  if (!out && !near && !item.updated_from) return null;
  return (
    <span className="mt-0.5 flex flex-wrap items-center gap-x-2 text-sm">
      {out ? <span className="font-medium text-warning">May be out of date</span> : null}
      {near ? <span className="text-ink-2">Related code changed</span> : null}
      {item.updated_from ? (
        <span className="text-ink-3">From {item.updated_from.slice(0, 7)}</span>
      ) : null}
    </span>
  );
}

export function EvidenceLine({ evidence }: { evidence: readonly Evidence[] }) {
  if (!evidence.length) return null;
  return (
    <span className="mt-0.5 flex flex-wrap items-center gap-x-1 gap-y-0.5 text-sm text-ink-3">
      {evidence.map((entry, index) => (
        <span key={index} className="inline-flex items-center gap-1">
          {index ? <span aria-hidden>·</span> : null}
          <b className="rounded-[5px] bg-sunk px-1.5 py-px font-mono text-[13px] font-medium text-ink-2">
            {EVIDENCE_LABEL[entry.kind]}
          </b>
          {entry.ref}
        </span>
      ))}
    </span>
  );
}

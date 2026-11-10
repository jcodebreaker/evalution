import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import type { Keep, Proof } from "../../lib/schema";

const KEEPS: { keep: Keep; label: string }[] = [
  { keep: "always", label: "Always" },
  { keep: "failed", label: "On failure" },
  { keep: "off", label: "Off" },
];

const PARTS: { part: keyof Proof; label: string }[] = [
  { part: "screenshots", label: "Screenshots" },
  { part: "video", label: "Video" },
];

/** What a try played through the page keeps, in a few words. */
export function proofWords(proof: Proof): string {
  const shots = {
    always: "a screenshot of every message",
    failed: "screenshots when a try fails",
    off: "no screenshots",
  }[proof.screenshots];
  const video = {
    always: "video of every try",
    failed: "video when a try fails",
    off: "no video",
  }[proof.video];
  return proof.screenshots === "off" && proof.video === "off"
    ? "Keeps no screenshots or video."
    : `Keeps ${shots} and ${video}.`;
}

/**
 * What a try played through the app's page keeps: its screenshots and its video, each always,
 * only when the try fails, or off. `aside` says more of a part beside it (a test's own over its
 * app's).
 */
export function ProofControl({
  proof,
  onChange,
  disabled = false,
  aside,
}: {
  proof: Proof;
  onChange: (part: keyof Proof, keep: Keep) => void;
  disabled?: boolean;
  aside?: (part: keyof Proof) => ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2" data-testid="proof-control">
      {PARTS.map(({ part, label }) => (
        <div key={part} className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="w-24 shrink-0 text-ink-2">{label}</span>
          <fieldset
            aria-label={label}
            className="m-0 flex gap-0.5 rounded-[7px] border-0 bg-sunk-2 p-[3px]"
          >
            {KEEPS.map(({ keep, label: word }) => (
              <button
                key={keep}
                type="button"
                aria-pressed={proof[part] === keep}
                disabled={disabled}
                onClick={() => {
                  if (proof[part] !== keep) onChange(part, keep);
                }}
                className={cn(
                  "h-[26px] rounded-[5px] px-2.5 text-[13px] focus-visible:outline-2 focus-visible:outline-tint disabled:opacity-60",
                  proof[part] === keep
                    ? "bg-group font-medium text-ink shadow-[0_1px_2px_rgba(20,22,43,0.14)]"
                    : "text-ink-2",
                )}
              >
                {word}
              </button>
            ))}
          </fieldset>
          {aside ? <span className="text-sm text-ink-3">{aside(part)}</span> : null}
        </div>
      ))}
    </div>
  );
}

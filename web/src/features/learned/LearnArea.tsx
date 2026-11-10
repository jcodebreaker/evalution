import { useState } from "react";
import { ago } from "../../lib/format";
import type { AreaState } from "../../lib/schema";
import { Button } from "../../ui/button";
import { Spinner } from "../../ui/loading";

/** Asks the server to learn one area (`force`: even when it hasn't moved). The answer is the
 * area as it stood: `current` spent nothing. */
export type LearnAreaFn = (area: string, force?: boolean) => Promise<AreaState>;

type Asked =
  | { kind: "idle" }
  | { kind: "asking" }
  | { kind: "asked" }
  | { kind: "current"; at: number | null }
  | { kind: "error"; text: string };

/** "Learn this area" for an area a learn listed and didn't learn. An area learned from what it is
 * now says so, with when, and "Learn it again anyway" as the deliberate way to spend. */
export function LearnAreaButton({
  area,
  onLearn,
  label = "Learn this area",
}: {
  area: string;
  onLearn: LearnAreaFn;
  label?: string;
}) {
  const [asked, setAsked] = useState<Asked>({ kind: "idle" });
  const ask = (force: boolean) => {
    setAsked({ kind: "asking" });
    onLearn(area, force).then(
      (found) =>
        setAsked(
          found.state === "current" && !force
            ? { kind: "current", at: found.learned_at }
            : { kind: "asked" },
        ),
      (error: unknown) =>
        setAsked({
          kind: "error",
          text: error instanceof Error ? error.message : "Couldn't ask. Try again.",
        }),
    );
  };
  if (asked.kind === "asking") {
    return (
      <span className="flex items-center gap-1.5 text-sm text-ink-3">
        <Spinner />
        Asking
      </span>
    );
  }
  if (asked.kind === "asked") return <span className="text-sm text-ink-3">Asked</span>;
  if (asked.kind === "current") {
    return (
      <span className="flex flex-wrap items-center justify-end gap-x-2 text-sm text-ink-3">
        <span>Current, learned {asked.at ? ago(asked.at) : "before"}</span>
        <Button variant="ghost" size="xs" onClick={() => ask(true)}>
          Learn it again anyway
        </Button>
      </span>
    );
  }
  return (
    <span className="flex flex-wrap items-center justify-end gap-x-2">
      {asked.kind === "error" ? (
        <span role="alert" className="text-sm text-fail">
          {asked.text}
        </span>
      ) : null}
      <Button variant="secondary" size="xs" onClick={() => ask(false)}>
        {label}
      </Button>
    </span>
  );
}

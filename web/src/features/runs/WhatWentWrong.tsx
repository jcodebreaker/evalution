import { useState } from "react";
import { errorText } from "../../lib/api";
import type { RunDetail } from "../../lib/schema";
import { ITS_READING } from "../../lib/verdict";
import { Fold, Group, GroupHeading, TextLink } from "../shared/group";
import { SuspectTag } from "../shared/marks";

/** The run's analysis: each pattern as one sentence, its tests as links, its detail folded. */
export function WhatWentWrong({
  run,
  onTest,
  onAnalyze,
}: {
  run: RunDetail;
  onTest: (id: string) => void;
  onAnalyze: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { analysis } = run;

  // A failing run is analyzed as it ends; only a failed analysis is tried again from here.
  if (!analysis) {
    if (!run.analysis_error) return null;
    const retry = async () => {
      setBusy(true);
      setError(null);
      try {
        await onAnalyze();
      } catch (failure) {
        setError(errorText(failure, "Couldn't analyze this run."));
      }
      setBusy(false);
    };
    return (
      <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-ink-3">
        <span>{error ?? run.analysis_error}</span>
        <TextLink disabled={busy} onClick={() => void retry()}>
          Try again
        </TextLink>
      </div>
    );
  }

  const byId = new Map(run.cases.map((row) => [row.case, row]));
  const byName = new Map(run.cases.map((row) => [row.name, row]));
  return (
    <section className="flex flex-col">
      <GroupHeading title={`What went wrong · ${ITS_READING.toLowerCase()}`} />
      <Group>
        {analysis.patterns.length ? (
          analysis.patterns.map((pattern, index) => (
            <div
              key={index}
              className="flex flex-col gap-1 border-separator px-3.5 py-2.5 not-first:border-t"
            >
              <Fold
                title={
                  <span className="flex flex-wrap items-center gap-2 text-[13.5px] text-foreground">
                    {pattern.title}
                    <SuspectTag suspect={pattern.suspected} />
                  </span>
                }
              >
                <p className="m-0 ps-5 text-[12.5px] leading-[1.45] text-ink-2">
                  {pattern.detail.text}
                </p>
              </Fold>
              {pattern.cases.length ? (
                <div className="flex flex-wrap gap-x-3 gap-y-0.5 ps-5 text-[12.5px]">
                  {pattern.cases.map((name) => {
                    const row = byId.get(name) ?? byName.get(name);
                    return row ? (
                      <TextLink key={name} onClick={() => onTest(row.case)}>
                        {row.name}
                      </TextLink>
                    ) : (
                      <span key={name} className="text-ink-3">
                        {name}
                      </span>
                    );
                  })}
                </div>
              ) : null}
            </div>
          ))
        ) : (
          <p className="m-0 px-3.5 py-3 text-[13.5px] leading-[1.45]">{analysis.summary.text}</p>
        )}
      </Group>
    </section>
  );
}

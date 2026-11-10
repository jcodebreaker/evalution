import { useState } from "react";
import { errorText } from "../../lib/api";
import type { RunDetail } from "../../lib/schema";
import { NOT_VERIFIED } from "../../lib/verdict";
import { TextLink } from "../shared/group";
import { SuspectTag } from "../shared/marks";
import { Card, CardHead, Unverified } from "./parts";

/** The run's analysis, as a card: each pattern as its claim in a sentence, what it rests on, and
 * the tests it names, each a way to that test. A claim nothing in the evidence backed says "Not
 * verified": once, by the title, when none is; else by each one that isn't. */
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
      <Card data-testid="what-went-wrong">
        <CardHead title="What went wrong" />
        <div className="flex flex-wrap items-center gap-2 px-5 pt-2 pb-5 text-[13px] text-ink-2">
          <span>{error ?? run.analysis_error}</span>
          <TextLink disabled={busy} onClick={() => void retry()}>
            Try again
          </TextLink>
        </div>
      </Card>
    );
  }

  const byId = new Map(run.cases.map((row) => [row.case, row]));
  const byName = new Map(run.cases.map((row) => [row.name, row]));
  const claims = analysis.patterns.length
    ? analysis.patterns.map((pattern) => pattern.detail.verified)
    : [analysis.summary.verified];
  const noneVerified = claims.every((verified) => !verified);
  return (
    <Card data-testid="what-went-wrong">
      <CardHead
        title="What went wrong"
        aside={noneVerified ? <Unverified>{NOT_VERIFIED}</Unverified> : null}
      />
      <div className="flex flex-col gap-4 px-5 pt-3 pb-5">
        {analysis.patterns.length ? (
          analysis.patterns.map((pattern, index) => {
            const rows = pattern.cases.map((name) => byId.get(name) ?? byName.get(name) ?? name);
            const first = rows.find((row) => typeof row !== "string");
            return (
              <div
                key={index}
                className="flex flex-col gap-1 not-first:border-t not-first:border-separator not-first:pt-4"
              >
                <p className="m-0 flex flex-wrap items-center gap-2 text-sm font-medium">
                  {pattern.title}
                  <SuspectTag suspect={pattern.suspected} />
                  {!noneVerified && !pattern.detail.verified ? (
                    <Unverified>{NOT_VERIFIED}</Unverified>
                  ) : null}
                </p>
                <p className="m-0 text-[13px] leading-[1.5] text-ink-2">{pattern.detail.text}</p>
                {rows.length ? (
                  <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px]">
                    {rows.map((row) =>
                      typeof row === "string" ? (
                        <span key={row} className="text-ink-3">
                          {row}
                        </span>
                      ) : (
                        <TextLink key={row.case} onClick={() => onTest(row.case)}>
                          {row.name}
                        </TextLink>
                      ),
                    )}
                  </div>
                ) : null}
                {first && typeof first !== "string" ? (
                  <button
                    type="button"
                    onClick={() => onTest(first.case)}
                    className="mt-1 w-fit text-[13px] font-medium text-tint-ink hover:underline hover:underline-offset-[3px] focus-visible:outline-2 focus-visible:outline-tint"
                  >
                    See why →
                  </button>
                ) : null}
              </div>
            );
          })
        ) : (
          <p className="m-0 text-[13px] leading-[1.5] text-ink-2">{analysis.summary.text}</p>
        )}
      </div>
    </Card>
  );
}

import { Check, ChevronRight, CircleAlert, Network, Play, Plus } from "lucide-react";
import { count, usd } from "../../lib/format";
import type { AgentMessage, RunSummary } from "../../lib/schema";
import { Button } from "../../ui/button";
import { Markdown } from "../../ui/markdown";
import { Fold, Group, TextLink } from "../shared/group";
import { MeaningChanges } from "../shared/MeaningChanges";
import { SuspectTag } from "../shared/marks";
import { ROW_LINK, RunLink } from "./RunLink";

/** The writer's one line after it learns an app ("I've learned X. Check what I understood."). */
const LEARNED = /^I['’]ve learned\b|Check what I understood/;

/** One message of the writer: what it read, its words, then its plan, added test, run and review. */
export function WriterReply({
  message,
  latest,
  runs,
  runLabel,
  testName,
  going,
  onReply,
  onApprove,
  onRun,
  onRunTest,
  onTest,
  onLearned,
}: {
  message: AgentMessage;
  /** The newest message while the writer waits: its actions are live. */
  latest: boolean;
  runs: readonly RunSummary[];
  runLabel: (run: string) => string;
  testName: (id: string) => string;
  going: boolean;
  onReply: (text: string) => void;
  onApprove: (planId: string) => void;
  onRun: (run: string) => void;
  onRunTest: (id: string) => void;
  onTest: (id: string, run?: string) => void;
  onLearned: () => void;
}) {
  const { plan, review, run, saved } = message;
  const changed = !!plan?.changes?.length;
  return (
    <div className="flex flex-col gap-2.5 text-[13.5px] leading-[1.55]">
      {message.read?.length ? (
        <details className="group text-[12px] text-ink-3">
          <summary className="flex cursor-default list-none items-center gap-1.5 hover:text-ink-2 [&::-webkit-details-marker]:hidden">
            <ChevronRight
              className="size-3.5 transition-transform group-open:rotate-90"
              aria-hidden
            />
            Read {message.read.length} {message.read.length === 1 ? "source" : "sources"}
          </summary>
          <ul className="m-0 flex list-none flex-col gap-0.5 p-0 pt-1 ps-5">
            {message.read.map((line, index) => (
              <li key={index}>{line}</li>
            ))}
          </ul>
        </details>
      ) : null}
      {message.text ? <Markdown text={message.text} /> : null}
      {LEARNED.test(message.text) ? (
        <Group>
          <button type="button" onClick={onLearned} className={ROW_LINK}>
            <Network className="size-3.5 shrink-0 text-ink-3" aria-hidden />
            <span className="min-w-0 flex-1 truncate font-medium">What it learned</span>
            <ChevronRight className="size-3.5 shrink-0 text-ink-3" aria-hidden />
          </button>
        </Group>
      ) : null}
      {plan ? (
        <Group>
          {plan.steps.map((step, index) => (
            <div
              key={index}
              className="flex items-start gap-2.5 border-separator px-3.5 py-2.5 not-first:border-t"
            >
              <span className="grid size-5 shrink-0 place-items-center rounded-md bg-selection text-[11px] font-semibold text-ink-2">
                {index + 1}
              </span>
              <div className="min-w-0 flex-1">
                <span className="font-medium">{step.title}</span>
                {step.note ? <p className="m-0 text-[12px] text-ink-2">{step.note}</p> : null}
              </div>
            </div>
          ))}
          {plan.timeout ? (
            <p className="m-0 border-t border-separator px-3.5 py-2.5 text-[12px] text-ink-2">
              Time limit {plan.timeout.seconds} s: {plan.timeout.from}
            </p>
          ) : null}
          {plan.flags?.map((flag) => (
            <p
              key={flag}
              className="m-0 flex items-start gap-2 border-t border-separator px-3.5 py-2.5 text-[13px]"
            >
              <CircleAlert className="mt-0.5 size-3.5 shrink-0 text-warning" aria-hidden />
              <span className="min-w-0">{flag}</span>
            </p>
          ))}
          {changed && plan.changes ? (
            <div className="border-t border-separator px-3.5 py-2.5">
              <MeaningChanges changes={plan.changes} />
            </div>
          ) : null}
          <div className="flex flex-wrap items-center gap-2.5 border-t border-separator px-3.5 py-3">
            <span className="text-[13px] text-ink-2 tabular-nums">
              {count(plan.tries, "try", "tries")} · about {usd(plan.usd)}
            </span>
            {latest && plan.id ? (
              <Button size="sm" className="ms-auto" onClick={() => onApprove(plan.id ?? "")}>
                <Plus />
                {plan.replaces ? "Replace the test" : "Add to tests"}
              </Button>
            ) : null}
          </div>
        </Group>
      ) : null}
      {saved ? (
        <Group className="flex flex-wrap items-center gap-2.5 px-3.5 py-3 text-[13.5px]">
          <Check className="size-4 shrink-0 text-success" aria-hidden />
          <span className="min-w-0 flex-1">
            Added:{" "}
            <button
              type="button"
              className="text-start font-medium hover:underline focus-visible:outline-2 focus-visible:outline-ring"
              onClick={() => onTest(saved)}
            >
              {testName(saved)}
            </button>
          </span>
          <Button size="sm" disabled={going} onClick={() => onRunTest(saved)}>
            <Play />
            Run
          </Button>
        </Group>
      ) : null}
      {run ? (
        <RunLink
          label={runLabel(run)}
          summary={runs.find((entry) => entry.run === run) ?? null}
          onClick={() => onRun(run)}
        />
      ) : null}
      {review ? (
        <div className="flex flex-col gap-2.5">
          <Markdown text={review.summary} />
          {review.findings.length ? (
            <Group>
              {review.findings.map((finding, index) => (
                <div
                  key={index}
                  className="flex flex-col gap-1 border-separator px-3.5 py-2.5 text-[13px] not-first:border-t"
                >
                  <div className="flex items-start gap-2">
                    <Markdown text={finding.text} className="flex-1" />
                    <SuspectTag suspect={finding.suspected} />
                  </div>
                  {finding.case ? (
                    <TextLink
                      className="w-fit text-[12px]"
                      onClick={() => onTest(finding.case ?? "", run)}
                    >
                      {testName(finding.case)}
                    </TextLink>
                  ) : null}
                  {finding.evidence?.length ? (
                    <Fold title="Evidence" className="text-[12px]">
                      <ul className="m-0 flex list-none flex-col gap-0.5 p-0 font-mono text-[11.5px] text-ink-2">
                        {finding.evidence.map((line, at) => (
                          <li key={at}>{line}</li>
                        ))}
                      </ul>
                    </Fold>
                  ) : null}
                </div>
              ))}
            </Group>
          ) : null}
          {review.proposal ? (
            <Group className="flex flex-wrap items-center gap-2.5 px-3.5 py-3 text-[13px]">
              <Markdown text={review.proposal} className="flex-1 basis-[200px]" />
              {latest ? (
                <Button size="sm" onClick={() => onReply("Do it")}>
                  Do it
                </Button>
              ) : null}
            </Group>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

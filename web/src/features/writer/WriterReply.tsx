import { ArrowRight, Check, ChevronRight, CircleAlert, Network, Play, Plus } from "lucide-react";
import { count } from "../../lib/format";
import type { AgentMessage, RunSummary } from "../../lib/schema";
import { Button } from "../../ui/button";
import { Markdown } from "../../ui/markdown";
import { Fold, Group, TextLink } from "../shared/group";
import { MeaningChanges } from "../shared/MeaningChanges";
import { SuspectTag } from "../shared/marks";
import { ROW_LINK, RunLink } from "./RunLink";

type WeakLine = NonNullable<NonNullable<AgentMessage["plan"]>["weak_lines"]>[number];

/** Why a rubric line was flagged, in the fewest words. */
const WHY: Record<WeakLine["why"], string> = {
  good_failed: "Fails a good reply",
  bad_passed: "Passes a bad reply",
  unsure: "Grader unsure",
};

/** What a flagged line's suggestion puts in its place, as the test file says it. */
function suggested(use: NonNullable<WeakLine["use"]>): string {
  if ("line" in use) return use.line;
  const [key, words] = "contains" in use ? ["contains", use.contains] : ["lacks", use.lacks];
  return `reply.${key} ${words.map((word) => `“${word}”`).join(", ")}`;
}

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
  onSwapLine,
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
  /** Puts a flagged rubric line's suggestion in its place, in the latest plan. */
  onSwapLine: (line: string) => void;
  onRun: (run: string) => void;
  onRunTest: (id: string) => void;
  onTest: (id: string, run?: string) => void;
  onLearned: () => void;
}) {
  const { plan, review, run, saved, isolation, page_map: pageMap } = message;
  const changed = !!plan?.changes?.length;
  return (
    <div className="flex flex-col gap-2.5 text-[13.5px] leading-[1.55]">
      {message.read?.length ? (
        <details className="group text-[12.5px] text-ink-3">
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
                {step.note ? <p className="m-0 text-[12.5px] text-ink-2">{step.note}</p> : null}
              </div>
            </div>
          ))}
          {plan.timeout ? (
            <p className="m-0 border-t border-separator px-3.5 py-2.5 text-[12.5px] text-ink-2">
              Time limit {plan.timeout.seconds} s: {plan.timeout.from}
            </p>
          ) : null}
          {plan.flags?.map((flag) => (
            <p
              key={flag}
              className="m-0 flex items-start gap-2 border-t border-separator px-3.5 py-2.5 text-[14px]"
            >
              <CircleAlert className="mt-0.5 size-3.5 shrink-0 text-warning" aria-hidden />
              <span className="min-w-0">{flag}</span>
            </p>
          ))}
          {plan.weak_lines?.map(({ line, why, use }) => (
            <div
              key={line}
              className="flex items-start gap-2 border-t border-separator px-3.5 py-2.5 text-[14px]"
            >
              <CircleAlert className="mt-0.5 size-3.5 shrink-0 text-warning" aria-hidden />
              <div className="min-w-0 flex-1">
                <p className="m-0">
                  {line} <span className="text-ink-3">· {WHY[why]}</span>
                </p>
                {use ? (
                  <p className="m-0 flex items-start gap-1 text-[12.5px] text-ink-2">
                    <ArrowRight className="mt-1 size-3 shrink-0" aria-hidden />
                    <span className="min-w-0">{suggested(use)}</span>
                  </p>
                ) : null}
              </div>
              {latest && use ? (
                <Button size="xs" variant="outline" onClick={() => onSwapLine(line)}>
                  Use this
                </Button>
              ) : null}
            </div>
          ))}
          {changed && plan.changes ? (
            <div className="border-t border-separator px-3.5 py-2.5">
              <MeaningChanges changes={plan.changes} />
            </div>
          ) : null}
          <div className="flex flex-wrap items-center gap-2.5 border-t border-separator px-3.5 py-3">
            <span className="text-[14px] text-ink-2 tabular-nums">
              {count(plan.tries, "try", "tries")}
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
      {pageMap ? (
        <PageMapProposal proposal={pageMap} latest={latest} onApprove={onApprove} />
      ) : null}
      {isolation ? (
        <IsolationProposal proposal={isolation} latest={latest} onApprove={onApprove} />
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
                  className="flex flex-col gap-1 border-separator px-3.5 py-2.5 text-[14px] not-first:border-t"
                >
                  <div className="flex items-start gap-2">
                    <Markdown text={finding.text} className="flex-1" />
                    <SuspectTag suspect={finding.suspected} />
                  </div>
                  {finding.case ? (
                    <TextLink
                      className="w-fit text-[12.5px]"
                      onClick={() => onTest(finding.case ?? "", run)}
                    >
                      {testName(finding.case)}
                    </TextLink>
                  ) : null}
                  {finding.evidence?.length ? (
                    <Fold title="Evidence" className="text-[12.5px]">
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
            <Group className="flex flex-wrap items-center gap-2.5 px-3.5 py-3 text-[14px]">
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

const PROPOSAL_STATUS: Record<string, string> = {
  draft: "Not approved yet",
  approved: "Approved",
  proven: "Proven on evalution's copy of the app",
  failed: "Not proven: the rehearsal found what it misses",
};

/** Where a page map's entry comes from, in a few words. */
function source(row: NonNullable<AgentMessage["page_map"]>["evidence"][number]): string {
  const where = row.file ? `${row.file}${row.line ? `:${row.line}` : ""}` : "";
  if (where && row.tried) return `${where}, found on the page`;
  return where || (row.tried ? "found on the page" : "");
}

/** The entry of a page map each of its plain-words rows is about. */
const ROW_ENTRY: Record<string, string> = {
  Opens: "url",
  "Signs in with": "sign_in",
  "Ready when it shows": "ready",
  "Message box": "actions.type",
  Send: "actions.send",
  "New chat": "actions.new_chat",
  Replies: "replies.in",
  "Busy while": "busy",
};

/** A page map the writer proposes, approved like a test plan. Approving it tries the map with one
 * real message on evalution's own copy; the map is used only once it's approved again on the Test
 * environment page. */
function PageMapProposal({
  proposal,
  latest,
  onApprove,
}: {
  proposal: NonNullable<AgentMessage["page_map"]>;
  latest: boolean;
  onApprove: (planId: string) => void;
}) {
  const evidence = new Map(proposal.evidence.map((row) => [row.entry, source(row)]));
  return (
    <Group data-testid="page-map-proposal">
      <p className="m-0 px-3.5 pt-2.5 text-[12.5px] text-ink-2">
        How tests would reach {proposal.target || "the app"}'s page
      </p>
      <ul className="m-0 flex list-none flex-col p-0">
        {proposal.described.map((row) => {
          const from = evidence.get(ROW_ENTRY[row.what] ?? "");
          return (
            <li
              key={row.what}
              className="flex flex-col border-separator px-3.5 py-2 text-[14px] not-first:border-t"
            >
              <span>
                <span className="text-ink-2">{row.what}:</span> {row.where}
              </span>
              {from ? <span className="font-mono text-[11.5px] text-ink-3">{from}</span> : null}
            </li>
          );
        })}
      </ul>
      <div className="flex flex-wrap items-center gap-2.5 border-t border-separator px-3.5 py-3">
        <span className="text-[14px] text-ink-2">
          {proposal.status === "draft"
            ? "Trying it sends one message to evalution's copy"
            : (PROPOSAL_STATUS[proposal.status] ?? proposal.status)}
        </span>
        {latest && proposal.status === "draft" ? (
          <Button size="sm" className="ms-auto" onClick={() => onApprove(proposal.id)}>
            <Check />
            Approve and try it
          </Button>
        ) : null}
      </div>
    </Group>
  );
}

/** A change the writer proposes to the isolation plan, approved like a test plan. Approving it
 * rehearses it on evalution's own copy of the app; the saved plan changes only once it's proven. */
function IsolationProposal({
  proposal,
  latest,
  onApprove,
}: {
  proposal: NonNullable<AgentMessage["isolation"]>;
  latest: boolean;
  onApprove: (planId: string) => void;
}) {
  return (
    <Group>
      <p className="m-0 px-3.5 pt-2.5 text-[12.5px] text-ink-2">
        Changes to the isolation plan, from {proposal.from}
      </p>
      <ul className="m-0 flex list-none flex-col p-0">
        {proposal.changes.map((change, index) => (
          <li key={index} className="border-separator px-3.5 py-2 text-[14px] not-first:border-t">
            {change}
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-center gap-2.5 border-t border-separator px-3.5 py-3">
        <span className="text-[14px] text-ink-2">
          {PROPOSAL_STATUS[proposal.status] ?? proposal.status}
        </span>
        {latest && proposal.status === "draft" ? (
          <Button size="sm" className="ms-auto" onClick={() => onApprove(proposal.id)}>
            <Check />
            Approve and rehearse
          </Button>
        ) : null}
      </div>
    </Group>
  );
}

import type { Finding } from "../../lib/schema";
import { ITS_READING, NOT_VERIFIED } from "../../lib/verdict";
import { TextLink } from "../shared/group";
import { SuspectTag } from "../shared/marks";

/** What the run's analysis found about this test, where the result is read. */
export function FindingNote({
  finding,
  onSpan,
}: {
  finding: Finding;
  onSpan?: (span: string) => void;
}) {
  const lead = finding.summary;
  const detail = finding.cause;
  if (!lead && !detail) return null;
  const said = [lead, detail].flatMap((claim) => (claim ? [claim] : []));
  const spans = said
    .flatMap((claim) => claim.refs)
    .filter((ref) => ref.startsWith("span:"))
    .map((ref) => ref.slice("span:".length));
  return (
    <div className="group-card flex flex-col gap-1 px-4 py-3 text-[13.5px]">
      <span className="text-[11.5px] font-semibold text-ink-3">
        {said.every((claim) => claim.verified) ? ITS_READING : `${ITS_READING} · ${NOT_VERIFIED}`}
      </span>
      <div className="flex items-start gap-2">
        <p className="m-0 min-w-0 flex-1 font-medium">{(lead ?? detail)?.text}</p>
        {finding.suspected ? <SuspectTag suspect={finding.suspected} /> : null}
      </div>
      {lead && detail ? <p className="m-0 text-[12.5px] text-ink-2">{detail.text}</p> : null}
      {finding.fix ? <p className="m-0 text-[12.5px] text-ink-2">{finding.fix}</p> : null}
      {onSpan
        ? [...new Set(spans)].map((span) => (
            <TextLink key={span} className="w-fit text-[12.5px]" onClick={() => onSpan(span)}>
              Evidence
            </TextLink>
          ))
        : null}
    </div>
  );
}

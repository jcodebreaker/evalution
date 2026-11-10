import { Chip } from "../shared/kit";
import { sentenceText, turnWords, type UiSentence } from "./ui-checks";

/** One UI check as a sentence, its element in a mono chip: "Shows `button 'Thumbs up'` under the
 * reply · turn 2"; a look carries the "model" tag. */
export function UiSentenceLine({
  sentence,
  withTurn,
}: {
  sentence: UiSentence;
  withTurn?: boolean;
}) {
  return (
    <span
      className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1"
      data-testid="ui-check"
      data-sentence={sentenceText(sentence, !!withTurn)}
    >
      <span>{sentence.lead}</span>
      {sentence.locator ? (
        <code className="rounded-md bg-sunk px-1.5 py-px font-mono text-[13px] [overflow-wrap:anywhere]">
          {sentence.locator}
        </code>
      ) : null}
      {sentence.where ? <span>{sentence.where}</span> : null}
      {withTurn ? <span className="text-ink-3">· {turnWords(sentence.turn)}</span> : null}
      {sentence.model ? <Chip className="h-6">model</Chip> : null}
    </span>
  );
}

/** A test's UI checks as rows, the way a step's checks are drawn. */
export function UiCheckList({
  sentences,
  withTurn,
}: {
  sentences: readonly UiSentence[];
  withTurn?: boolean;
}) {
  if (!sentences.length) return null;
  return (
    <div className="group-card">
      {sentences.map((sentence, index) => (
        <div
          key={index}
          className="grid min-w-0 grid-cols-[12px_minmax(0,1fr)] items-start gap-2.5 border-separator px-3.5 py-2.5 text-sm not-first:border-t"
        >
          <span className="flex justify-center pt-[7px]">
            <span aria-hidden className="size-2 rounded-full bg-rule-strong" />
          </span>
          <UiSentenceLine sentence={sentence} withTurn={withTurn} />
        </div>
      ))}
    </div>
  );
}

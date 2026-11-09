import { count } from "../../lib/format";
import type { Health } from "../../lib/schema";
import { TextLink } from "../shared/group";
import { OutOfDate } from "../shared/KnowledgeUpdate";

/** What a commit did to what it knows, once; and how much may be out of date, until updated. */
export function KnowledgeLine({
  target,
  knowledge,
  onReview,
  onUpdated,
}: {
  target: string;
  knowledge: Health["knowledge"];
  onReview: () => void;
  onUpdated: () => void;
}) {
  const { last } = knowledge;
  return (
    <>
      {last ? (
        <p className="m-0 text-[14px] text-ink-2">
          {last.changed
            ? `It updated what it knows from ${last.commit}: ${count(last.changed, "claim")} changed`
            : `It checked what it knows against ${last.commit}`}
          {last.flagged ? (
            <>
              {" · "}
              <TextLink onClick={onReview}>
                {last.flagged} of your corrections may be out of date
              </TextLink>
            </>
          ) : null}
        </p>
      ) : null}
      <OutOfDate target={target} knowledge={knowledge} onUpdated={onUpdated} />
    </>
  );
}

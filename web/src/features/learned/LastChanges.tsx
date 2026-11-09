import type { Understanding } from "../../lib/schema";
import { Button } from "../../ui/button";
import { Fold } from "../shared/group";

/** What the last version changed, and undoing it when it came from a commit. */
export function LastChanges({
  knowledge,
  onUndo,
}: {
  knowledge: Understanding["knowledge"];
  onUndo: () => void;
}) {
  const { changes, can_undo: canUndo } = knowledge;
  if (!changes.length && !canUndo) return null;
  return (
    <Fold title={`What changed · ${changes.length}`}>
      <ul className="m-0 flex list-none flex-col gap-2 p-0 px-1 text-[14px]">
        {changes.map((change, index) => (
          <li key={index} className="flex flex-col gap-0.5">
            {change.before ? <s className="text-ink-3">{change.before}</s> : null}
            {change.after ? <span>{change.after}</span> : null}
          </li>
        ))}
      </ul>
      {canUndo ? (
        <Button variant="ghost" className="mt-2 text-ink-2" onClick={onUndo}>
          Undo
        </Button>
      ) : null}
    </Fold>
  );
}

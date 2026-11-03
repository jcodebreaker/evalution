import type { MeaningChange } from "../../lib/schema";

const shown = (value: unknown) =>
  value === null || value === undefined ? "Nothing" : JSON.stringify(value);

/** What a save changes in what a test checks, and what that means for its results. */
export function MeaningChanges({ changes }: { changes: MeaningChange[] }) {
  return (
    <div className="flex flex-col gap-2 text-[13px]">
      <p className="m-0 font-medium">
        This changes what the test checks. Earlier results won't be comparable.
      </p>
      <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
        {changes.map((change) => (
          <li key={change.what} className="min-w-0">
            <span className="text-ink-2">{change.what}</span>
            <div className="font-mono text-[11.5px] break-words text-ink-3">
              {shown(change.before)}
            </div>
            <div className="font-mono text-[11.5px] break-words">{shown(change.after)}</div>
          </li>
        ))}
      </ul>
    </div>
  );
}

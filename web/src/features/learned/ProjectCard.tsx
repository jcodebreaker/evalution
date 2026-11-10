import type { Understanding } from "../../lib/schema";

/** The app at a glance, as the understanding carries it: its name, one line, stack and numbers.
 * What it learned draws it in its head and figures; the app picker, by `CardRow`. */
export type Card = Understanding["card"];

/** An app in the picker: its name and what it is. */
export function CardRow({ name, card }: { name: string; card: Card | null }) {
  return (
    <span className="flex min-w-0 max-w-72 flex-col">
      <span className="truncate font-medium">{name}</span>
      {card?.one_line ? (
        <span className="truncate text-[13px] text-ink-3">{card.one_line}</span>
      ) : null}
    </span>
  );
}

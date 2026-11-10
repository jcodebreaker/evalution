import type { ElementCount, PageElement, UiChecks } from "../../lib/schema";

// A test's UI checks in plain words: "Shows button 'Thumbs up' under the reply · turn 2".

/** One UI check as a sentence: its lead, the element as a locator (none for a look), and where. */
export type UiSentence = {
  kind: "shows" | "hides" | "looks";
  lead: string;
  locator: string | null;
  where: string;
  /** None: every turn with a reply. */
  turn: number | null;
  /** Graded by a model from the screenshot, not decided by code. */
  model: boolean;
};

const quoted = (text: string) => `'${text}'`;

/** An element as its locator: `button 'Thumbs up'`, `test id 'like'`, `text 'Saved'`. */
export function locatorOf(element: PageElement): string {
  const parts: string[] = [];
  if (element.role || element.name) {
    parts.push(
      [element.role, element.name ? quoted(element.name) : null].filter(Boolean).join(" "),
    );
  }
  if (element.test_id) parts.push(`test id ${quoted(element.test_id)}`);
  if (element.text) parts.push(`text ${quoted(element.text)}`);
  return parts.join(", ") || "an element";
}

/** How many, in words: `3`, `at least 2`, `2 to 4`; empty for one or more. */
export function countWords(count: ElementCount | undefined): string {
  if (count === undefined) return "";
  if (typeof count === "number") return String(count);
  const { min, max } = count;
  if (min !== undefined && max !== undefined) return min === max ? String(min) : `${min} to ${max}`;
  if (min !== undefined) return `at least ${min}`;
  if (max !== undefined) return `at most ${max}`;
  return "";
}

const whereOf = (element: PageElement) =>
  element.within === "page" ? "on the page" : "under the reply";

/** Each UI check of a test as a sentence, in the order its block says them. */
export function uiSentences(checks: UiChecks | null | undefined): UiSentence[] {
  if (!checks) return [];
  const element = (kind: "shows" | "hides", found: PageElement): UiSentence => {
    const many = countWords(found.count);
    const lead = kind === "shows" ? "Shows" : "Doesn't show";
    return {
      kind,
      lead: many ? `${lead} ${many}` : lead,
      locator: locatorOf(found),
      where: whereOf(found),
      turn: found.turn ?? null,
      model: false,
    };
  };
  return [
    ...checks.shows.map((found) => element("shows", found)),
    ...checks.hides.map((found) => element("hides", found)),
    ...checks.looks.map(
      (look): UiSentence => ({
        kind: "looks",
        lead: `Looks: ${look.question}`,
        locator: null,
        where: "",
        turn: look.turn ?? null,
        model: true,
      }),
    ),
  ];
}

/** Which turn a check is for, in a few words: `turn 2`, `every reply`. */
export const turnWords = (turn: number | null) => (turn === null ? "every reply" : `turn ${turn}`);

/** A sentence as plain text: "Shows button 'Thumbs up' under the reply · turn 2". */
export function sentenceText(sentence: UiSentence, withTurn = true): string {
  const words = [sentence.lead, sentence.locator, sentence.where].filter(Boolean).join(" ");
  return withTurn ? `${words} · ${turnWords(sentence.turn)}` : words;
}

/** The checks by the turn they're for, and those for every reply. */
export function byTurn(sentences: readonly UiSentence[]): {
  turns: Map<number, UiSentence[]>;
  every: UiSentence[];
} {
  const turns = new Map<number, UiSentence[]>();
  const every: UiSentence[] = [];
  for (const sentence of sentences) {
    if (sentence.turn === null) every.push(sentence);
    else turns.set(sentence.turn, [...(turns.get(sentence.turn) ?? []), sentence]);
  }
  return { turns, every };
}

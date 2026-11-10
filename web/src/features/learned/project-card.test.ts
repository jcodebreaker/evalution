import { expect, it } from "vitest";
import { ChangesSchema, type Understanding } from "../../lib/schema";
import { changedSinceLearned } from "./ProjectCard";

type Shown = Pick<Understanding, "card" | "changes">;

function shown(status: Understanding["card"]["status"], changes: number, moved?: unknown): Shown {
  const card = { status, changes } as Understanding["card"];
  return { card, changes: moved === undefined ? undefined : ChangesSchema.parse(moved) };
}

it("counts changed since learned from the one changes signal, the card's count from before", () => {
  const learned = { commit: "abc", files: ["lib/b.py"], file_count: 1 };
  expect(changedSinceLearned(shown("out_of_date", 3, { since: { learned } }))).toBe(1);
  // A refresh covered it: the signal says nothing moved, whatever the card counted.
  const covered = { ...learned, files: [], file_count: 0 };
  expect(changedSinceLearned(shown("out_of_date", 1, { since: { learned: covered } }))).toBeNull();
  expect(changedSinceLearned(shown("out_of_date", 2))).toBe(2);
  expect(changedSinceLearned(shown("up_to_date", 0))).toBeNull();
});

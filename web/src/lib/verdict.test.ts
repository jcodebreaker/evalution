import { describe, expect, it } from "vitest";
import { RunTestSchema } from "./schema";
import { unjudgedWords } from "./verdict";

describe("Why a test couldn't be judged", () => {
  it("says plainly that two tools share a name and the test must say which", () => {
    const test = RunTestSchema.parse({
      case: "c",
      name: "Finds a customer",
      verdict: "unjudged",
      unjudged: ["ambiguous_tool"],
      passed: 0,
      tries: 1,
    });
    expect(unjudgedWords(test.unjudged)).toBe("two tools share this name: the test must say which");
  });
});

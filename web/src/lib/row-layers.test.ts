import { expect, it } from "vitest";
import { asSentence } from "./format";
import { layerLine } from "./verdict";

it("a row says the page's health apart, as the try's banner does, and only when it failed", () => {
  const base = { stored: "absent", answered: "unjudged", shown: "unjudged" } as const;
  expect(layerLine({ ...base, page_health: "fail" })).toBe("Answered ? · Shown ? · Page healthy ✕");
  expect(layerLine({ ...base, page_health: "pass" })).toBe("Answered ? · Shown ?");
  expect(layerLine(base)).toBe("Answered ? · Shown ?");
});

it("a reason reads as a sentence of its own before the banner's next one", () => {
  expect(asSentence("the app answered a request from the page with an error, 503")).toBe(
    "The app answered a request from the page with an error, 503.",
  );
  expect(asSentence("Already a sentence.")).toBe("Already a sentence.");
});

it("the model list's failure reads as its own sentence, then what to do", () => {
  expect(`${asSentence("The key was refused")} So`).toBe("The key was refused. So");
});

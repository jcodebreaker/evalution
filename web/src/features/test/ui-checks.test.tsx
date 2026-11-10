// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { PlanStepSchema, UiChecksSchema } from "../../lib/schema";
import { Steps } from "./Steps";
import { sentenceText, uiSentences } from "./ui-checks";

afterEach(cleanup);

const CHECKS = UiChecksSchema.parse({
  shows: [
    { role: "button", name: "Thumbs up", within: "reply", turn: 2 },
    { role: "listitem", count: 3, within: "page" },
    { test_id: "source", count: { min: 2 }, turn: 1 },
  ],
  hides: [{ text: "Something went wrong", within: "page" }],
  looks: [{ question: "Is there a thumbs-up icon under the reply?", turn: 2 }],
});

describe("A test's UI checks, in words", () => {
  it("says each kind as a short sentence with its locator", () => {
    expect(uiSentences(CHECKS).map((sentence) => sentenceText(sentence))).toEqual([
      "Shows button 'Thumbs up' under the reply · turn 2",
      "Shows 3 listitem on the page · every reply",
      "Shows at least 2 test id 'source' under the reply · turn 1",
      "Doesn't show text 'Something went wrong' on the page · every reply",
      "Looks: Is there a thumbs-up icon under the reply? · turn 2",
    ]);
    const look = uiSentences(CHECKS)[4];
    expect([look?.model, look?.locator]).toEqual([true, null]);
  });

  it("has none for a test without a page block", () => {
    expect(uiSentences(null)).toEqual([]);
  });

  it("puts each under its turn's reply step, the rest under Every reply", () => {
    const steps = [
      { kind: "say", message: "Hi" },
      { kind: "reply", title: "Greets" },
      { kind: "say", message: "Thanks" },
      { kind: "reply", title: "Says you're welcome" },
      { kind: "passes", title: "All must checks hold" },
    ].map((step) => PlanStepSchema.parse(step));
    render(
      <Steps steps={steps} names={{}} appName="cartly" result={false} ui={uiSentences(CHECKS)} />,
    );
    const items = screen.getAllByRole("listitem");
    const texts = (index: number) =>
      within(items[index] as HTMLElement)
        .queryAllByTestId("ui-check")
        .map((line) => line.getAttribute("data-sentence"));
    expect(items).toHaveLength(6);
    expect(texts(1)).toEqual(["Shows at least 2 test id 'source' under the reply"]);
    expect(texts(3)).toEqual([
      "Shows button 'Thumbs up' under the reply",
      "Looks: Is there a thumbs-up icon under the reply?",
    ]);
    expect(within(items[3] as HTMLElement).getByText("model")).toBeTruthy();
    expect(within(items[3] as HTMLElement).getByText("button 'Thumbs up'").tagName).toBe("CODE");
    // "Every reply" comes before "Passes when".
    expect(within(items[4] as HTMLElement).getByText("Every reply")).toBeTruthy();
    expect(texts(4)).toHaveLength(2);
  });
});

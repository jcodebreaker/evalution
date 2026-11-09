// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { AgentSessionSchema, LearnStageSchema, TurnUsageSchema } from "../../lib/schema";
import { LastLearn, lastLearn, stageText } from "./LastLearn";

afterEach(cleanup);

const stage = (name: string, seconds: number, calls = 0, tokensIn = 0, tokensOut = 0) =>
  LearnStageSchema.parse({
    stage: name,
    seconds,
    calls,
    tokens_in: tokensIn,
    tokens_out: tokensOut,
  });

const MAP = stage("code map", 199.4);
const READ = stage("read", 1560, 48, 1_150_000, 50_000);
const OVERVIEW = stage("overview", 31, 1, 9_000, 2_000);
const LEARN = TurnUsageSchema.parse({
  learn: true,
  at: 10,
  calls: 70,
  stages: [MAP, READ, stage("areas", 420, 21, 300_000, 40_000), OVERVIEW],
});
/** A learn that found nothing changed, after it. */
const NOTHING = TurnUsageSchema.parse({ learn: true, at: 20, stages: [stage("other", 0.2)] });
const CHAT = TurnUsageSchema.parse({ at: 30, calls: 2 });

it("says each stage in plain words: its time, then its calls and tokens", () => {
  expect(stageText(MAP)).toBe("Code map 199 s");
  expect(stageText(READ)).toBe("Read 26 min, 48 calls, 1.2M tokens");
  expect(stageText(OVERVIEW)).toBe("Overview 31 s, 1 call, 11k tokens");
});

it("shows the last learn that did something, not a chat or a learn that found nothing", () => {
  expect(lastLearn([LEARN, NOTHING, CHAT])).toBe(LEARN);
  expect(lastLearn([CHAT])).toBeNull();
  const session = AgentSessionSchema.parse({
    id: "a1",
    target: "bot",
    set: "main",
    status: "idle",
    messages: [],
    usage: [LEARN, NOTHING, CHAT],
  });
  render(<LastLearn session={session} />);
  const line = /Code map 199 s · Read 26 min, 48 calls, 1.2M tokens · Areas 7 min, 21 calls/;
  expect(screen.getByText(line)).toBeTruthy();
  expect(screen.getByText("Last learn:")).toBeTruthy();
});

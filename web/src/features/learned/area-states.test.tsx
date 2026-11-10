// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import { AgentSessionSchema } from "../../lib/schema";

afterEach(() => vi.unstubAllGlobals());

it("reads a session during a learn with an area outside the agent and one left for later", () => {
  const session = AgentSessionSchema.parse({
    id: "a1",
    target: "bot",
    set: "main",
    status: "working",
    messages: [],
    learn: {
      at: 1,
      stage: "areas",
      areas: [
        { id: "agents", files: 3, state: "running" },
        { id: "ios", files: 101, state: "outside" },
        { id: "extra", files: 4, state: "later" },
      ],
    },
  });
  expect(session.learn?.areas.map((area) => area.state)).toEqual(["running", "outside", "later"]);
});

it("Learn this area on an area outside the agent resolves with its state", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            area: { id: "ios", state: "outside", weight: 0, files: 101, learned_at: null },
            session: null,
          }),
          { headers: { "content-type": "application/json" } },
        ),
    ),
  );
  const answer = await api.learnArea("bot", "ios", {});
  expect(answer.area.state).toBe("outside");
});

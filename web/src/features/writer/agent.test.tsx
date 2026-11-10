// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import type { AgentSession } from "../../lib/schema";
import { useAgent } from "./agent";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  localStorage.clear();
});

const session = (id: string, target: string, status: AgentSession["status"]): AgentSession => ({
  id,
  target,
  set: "main",
  status,
  messages: [],
  activity: [],
  learn: null,
  usage: [],
});

it("keeps what waits for an app's writer across a switch, and sends it when that app is back", async () => {
  localStorage.setItem("evalution.writer.shop", "s1");
  localStorage.setItem("evalution.writer.relay", "s2");
  // The shop's writer works until the person has gone to the relay and come back.
  let shopWorks = true;
  vi.spyOn(api.agent, "session").mockImplementation(async (id) =>
    id === "s1"
      ? session("s1", "shop", shopWorks ? "working" : "idle")
      : session("s2", "relay", "idle"),
  );
  const send = vi
    .spyOn(api.agent, "send")
    .mockImplementation(async (id) => session(id, id === "s1" ? "shop" : "relay", "idle"));
  const { result, rerender } = renderHook(({ target }) => useAgent({ target, set: "main" }), {
    initialProps: { target: "shop" },
  });
  await waitFor(() => expect(result.current.session?.status).toBe("working"));
  await act(() => result.current.ask("Write one for the refund"));
  expect(result.current.queued.map((entry) => entry.text)).toEqual(["Write one for the refund"]);

  rerender({ target: "relay" });
  await waitFor(() => expect(result.current.session?.id).toBe("s2"));
  expect(result.current.queued).toEqual([]);
  expect(send).not.toHaveBeenCalled();

  shopWorks = false;
  rerender({ target: "shop" });
  await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
  expect(send.mock.calls[0]?.slice(0, 2)).toEqual(["s1", "Write one for the refund"]);
  await waitFor(() => expect(result.current.queued).toEqual([]));
});

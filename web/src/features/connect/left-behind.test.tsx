// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import { TargetDetailSchema } from "../../lib/schema";
import { TargetFields } from "./TargetFields";
import { leftBehindLine } from "./target-form";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const saved = TargetDetailSchema.parse({
  name: "shop",
  adapter: "http",
  options: { send: { method: "POST", url: "http://shop.test/chat", body: { m: "{message}" } } },
  phoenix: { url: "http://phoenix.test:6006", project: "shop" },
  secrets: ["authorization", "phoenix_api_key"],
});

it("names the stored secret a save won't keep before the save, and saves in one click", async () => {
  vi.spyOn(api, "traces").mockReturnValue(new Promise(() => undefined));
  vi.spyOn(api, "adapters").mockReturnValue(new Promise(() => undefined));
  const onSave = vi.fn(async () => undefined);
  render(<TargetFields saved={saved} action="Save" onSave={onSave} />);
  expect(screen.queryByTestId("left-behind")).toBeNull();

  // Another host for the app: its key isn't sent there; Phoenix's stays with Phoenix.
  fireEvent.change(screen.getByPlaceholderText("http://localhost:3000/chat"), {
    target: { value: "http://api.shop.test/chat" },
  });
  expect(screen.getByTestId("left-behind").textContent).toBe(
    "Saving won't keep the stored Authorization: it isn't sent to a new address. Type it again to keep it.",
  );
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("names each secret left behind, and none when there are none", () => {
  expect(leftBehindLine([])).toBeNull();
  expect(leftBehindLine(["authorization", "phoenix_api_key"])).toMatch(
    /^Saving won't keep the stored Authorization and Phoenix API key: /,
  );
});

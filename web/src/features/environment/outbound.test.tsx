// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { IsolationViewSchema } from "../../lib/schema";
import { TestEnvironment } from "./TestEnvironment";

type Answer = { status?: number; body?: unknown };
type Call = { method: string; path: string; body: unknown };

let calls: Call[] = [];

function serve(routes: Record<string, Answer | ((body: unknown) => Answer)>) {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init: RequestInit = {}) => {
      const method = init.method ?? "GET";
      const body = init.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ method, path, body });
      const route = routes[`${method} ${path.split("?")[0]}`];
      const { status = 200, body: answer = {} } =
        (typeof route === "function" ? route(body) : route) ?? {};
      return new Response(JSON.stringify(answer), {
        status,
        headers: { "content-type": "application/json" },
      });
    }),
  );
}

beforeEach(() => window.history.replaceState(null, "", "/"));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const T = "/v1/targets/cartly";
const OUTBOUND = [
  { name: "STRIPE_SECRET_KEY", kind: "payment", allowed: false },
  { name: "SENDGRID_API_KEY", kind: "mail", allowed: true },
];
const PROVEN = {
  target: "cartly",
  status: "proven",
  version: 2,
  can_run: true,
  plan: {
    stores: [
      { id: "pg", kind: "postgres", locator: "container:db", strategy: "relocate", seen: true },
      { id: "cache", kind: "redis", locator: "container:cache", strategy: "relocate" },
    ],
    exclude: ["Traces"],
  },
  outbound: OUTBOUND,
  put_back: { hot: "proven", app_holds_state: false, restart: false },
};
const STORES = [
  { id: "pg", kind: "postgres", name: "cartly", holds: "Orders, profiles" },
  { id: "cache", kind: "redis", name: "cache", holds: "Carts" },
];

function page(isolation: unknown, more: Record<string, Answer | ((body: unknown) => Answer)> = {}) {
  serve({
    [`GET ${T}/isolation`]: { body: isolation },
    [`GET ${T}/isolation/setup`]: { body: {} },
    [`GET ${T}/health`]: { body: { isolation: { status: "proven", stores: STORES } } },
    ...more,
  });
  render(<TestEnvironment target="cartly" mode="page" />);
}

it("reads what leaves the copy and how it is put back, and an older server's view without them", () => {
  const read = IsolationViewSchema.parse(PROVEN);
  expect(read.outbound[0]).toEqual({ name: "STRIPE_SECRET_KEY", kind: "payment", allowed: false });
  expect(read.put_back).toEqual({ hot: "proven", app_holds_state: false, restart: false });
  const old = IsolationViewSchema.parse({ target: "cartly" });
  expect(old.outbound).toEqual([]);
  expect(old.put_back).toBeNull();
});

it("strikes through each blocked setting in the copy's row, and leaves an allowed one plain", async () => {
  page(PROVEN);
  const row = await screen.findByRole("list", { name: "Leaves the copy" });
  const stripe = within(row).getByText("STRIPE_SECRET_KEY");
  expect(stripe.tagName).toBe("S");
  expect(stripe.closest("li")?.hasAttribute("data-blocked")).toBe(true);
  const mail = within(row).getByText("SENDGRID_API_KEY");
  expect(mail.tagName).not.toBe("S");
  expect(within(row).getByText("model")).toBeTruthy();
  expect(within(row).getByText("traces")).toBeTruthy();
});

it("a switch per setting allows it to leave the copy, sent by name, and shows the answer", async () => {
  page(PROVEN, {
    [`PUT ${T}/isolation/outbound`]: () => ({
      body: { outbound: OUTBOUND.map((entry) => ({ ...entry, allowed: true })) },
    }),
  });
  const card = await screen.findByRole("generic", { name: "Leaves the copy" });
  const stripe = within(card).getByRole("switch", { name: "STRIPE_SECRET_KEY" });
  expect(stripe.getAttribute("aria-checked")).toBe("false");
  expect(
    within(card).getByRole("switch", { name: "SENDGRID_API_KEY" }).getAttribute("aria-checked"),
  ).toBe("true");
  fireEvent.click(stripe);
  await waitFor(() =>
    expect(calls).toContainEqual({
      method: "PUT",
      path: `${T}/isolation/outbound`,
      body: { name: "STRIPE_SECRET_KEY", allowed: true },
    }),
  );
  await waitFor(() =>
    expect(
      within(card).getByRole("switch", { name: "STRIPE_SECRET_KEY" }).getAttribute("aria-checked"),
    ).toBe("true"),
  );
});

it("says when a switch can't change during a run", async () => {
  page(PROVEN, { [`PUT ${T}/isolation/outbound`]: { status: 409, body: {} } });
  const card = await screen.findByRole("generic", { name: "Leaves the copy" });
  fireEvent.click(within(card).getByRole("switch", { name: "STRIPE_SECRET_KEY" }));
  await within(card).findByText("Busy now: a run or the setup. Try when it's done.");
});

it("lists what it puts back before every try, and that the app stays up", async () => {
  page(PROVEN);
  const table = await screen.findByRole("table");
  const rows = within(table).getAllByRole("row").slice(1);
  expect(rows.map((row) => row.textContent)).toEqual([
    expect.stringContaining("New copy from a template, app up"),
    expect.stringContaining("Restarted empty"),
    expect.stringContaining("Left alone, as evidence"),
  ]);
  expect(rows[0]?.textContent).toContain("Orders, profiles");
  expect(within(rows[0] as HTMLElement).getByLabelText("Held")).toBeTruthy();
  expect(screen.getByText("App stays up")).toBeTruthy();
  expect(screen.getByText("Proven · plan v2")).toBeTruthy();
});

// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ConnectView } from "./ConnectView";
import { sourceKind } from "./source";

type Answer = { status?: number; body?: unknown };
type Call = { method: string; path: string; body: unknown };

let calls: Call[] = [];

function serve(routes: Record<string, Answer | (() => Answer)>) {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init: RequestInit = {}) => {
      const method = init.method ?? "GET";
      calls.push({ method, path, body: init.body ? JSON.parse(String(init.body)) : undefined });
      const route = routes[`${method} ${path.split("?")[0]}`];
      const { status = 200, body = {} } = (typeof route === "function" ? route() : route) ?? {};
      return new Response(JSON.stringify(body), {
        status,
        headers: { "content-type": "application/json" },
      });
    }),
  );
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  localStorage.clear();
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function connect() {
  render(
    <ConnectView
      first
      onReady={async () => {}}
      onLearn={() => {}}
      onLearned={() => {}}
      onAsk={async () => {}}
    />,
  );
}

it("reads what the field names: a folder, a repository, a pull request, a ref or an address", () => {
  expect(sourceKind("~/code/cartly")).toBe("folder");
  expect(sourceKind("./cartly")).toBe("folder");
  expect(sourceKind("acme/cartly")).toBe("repo");
  expect(sourceKind("github.com/acme/cartly")).toBe("repo");
  expect(sourceKind("https://github.com/acme/cartly.git")).toBe("repo");
  expect(sourceKind("acme/cartly#412")).toBe("pull");
  expect(sourceKind("https://github.com/acme/cartly/pull/412")).toBe("pull");
  expect(sourceKind("acme/cartly@feature-x")).toBe("ref");
  expect(sourceKind("http://localhost:3000/chat")).toBe("address");
  expect(sourceKind("localhost:3000")).toBe("address");
  expect(sourceKind("cartly")).toBe("folder");
});

it("one field: a GitHub pull request chip fills it, the icon says what it is, Connect sends it", async () => {
  serve({
    "POST /v1/targets/discover": {
      body: { id: "d1", folder: "/data/code/cartly", status: "working", steps: [], questions: [] },
    },
    "GET /v1/targets/discover/d1": {
      body: { id: "d1", folder: "/data/code/cartly", status: "working", steps: [], questions: [] },
    },
  });
  connect();
  expect(screen.getByRole("heading", { name: "Connect your first app" })).toBeTruthy();
  const field = screen.getByLabelText("Folder, GitHub repository or address") as HTMLInputElement;
  expect(screen.getByRole("img", { name: "Folder" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "owner/repo#412" }));
  expect(field.value).toBe("owner/repo#412");
  expect(screen.getByRole("img", { name: "Pull request" })).toBeTruthy();
  fireEvent.change(field, { target: { value: "acme/cartly" } });
  expect(screen.getByRole("img", { name: "GitHub repository" })).toBeTruthy();
  fireEvent.change(field, { target: { value: "acme/cartly#412" } });
  fireEvent.click(screen.getAllByRole("button", { name: "Connect" })[0] as HTMLElement);
  await waitFor(() =>
    expect(calls).toContainEqual({
      method: "POST",
      path: "/v1/targets/discover",
      body: { path: "acme/cartly#412" },
    }),
  );
});

it("a recent folder connects at once, and the technical form stays under Details", async () => {
  localStorage.setItem("evalution.folders", JSON.stringify(["/Users/me/code/cartly"]));
  serve({
    "POST /v1/targets/discover": {
      body: {
        id: "d1",
        folder: "/Users/me/code/cartly",
        status: "failed",
        steps: [],
        questions: [],
      },
    },
  });
  connect();
  expect(screen.getByText("Details")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "cartly" }));
  await waitFor(() =>
    expect(calls).toContainEqual({
      method: "POST",
      path: "/v1/targets/discover",
      body: { path: "/Users/me/code/cartly" },
    }),
  );
});

it("reads the folder one answer at a time: a slow one is never asked over", async () => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  try {
    const working = { id: "d1", folder: "/c/cartly", status: "working", steps: [], questions: [] };
    let reads = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string, init: RequestInit = {}) => {
        if (init.method === "POST") {
          const headers = { "content-type": "application/json" };
          return new Response(JSON.stringify(working), { status: 200, headers });
        }
        if (!path.startsWith("/v1/targets/discover/d1")) {
          return new Response("{}", {
            status: 404,
            headers: { "content-type": "application/json" },
          });
        }
        reads += 1;
        return new Promise<Response>(() => {}); // a slow clone: this read never answers
      }),
    );
    connect();
    const field = screen.getByLabelText("Folder, GitHub repository or address");
    fireEvent.change(field, { target: { value: "/c/cartly" } });
    fireEvent.submit(field.closest("form") as HTMLFormElement);
    await waitFor(() =>
      expect(screen.queryByLabelText("Folder, GitHub repository or address")).toBeNull(),
    );
    vi.advanceTimersByTime(700 * 5);
    expect(reads).toBe(1);
  } finally {
    vi.useRealTimers();
  }
});

it("says so when learning the app couldn't start, instead of waiting on it", async () => {
  const ready = {
    id: "d1",
    folder: "/c/cartly",
    status: "ready",
    steps: [],
    questions: [],
    target: { name: "cartly", adapter: "http", options: {} },
  };
  serve({
    "POST /v1/targets/discover": { body: ready },
    "GET /v1/targets/discover/d1": { body: ready },
    "POST /v1/targets/discover/d1/accept": { body: { name: "cartly", adapter: "http" } },
    "GET /v1/targets": { body: { data: [] } },
    "GET /v1/settings/model": {
      body: {
        provider: "anthropic",
        base_url: "https://api.anthropic.test",
        strong: "strong-model",
        light: "light-model",
        key_set: true,
        source: "page",
      },
    },
  });
  const onLearn = vi.fn(async () => {
    throw new Error("the server said no");
  });
  render(
    <ConnectView
      first
      onReady={async () => {}}
      onLearn={onLearn}
      onLearned={() => {}}
      onAsk={async () => {}}
    />,
  );
  const field = screen.getByLabelText("Folder, GitHub repository or address");
  fireEvent.change(field, { target: { value: "/c/cartly" } });
  fireEvent.submit(field.closest("form") as HTMLFormElement);
  await waitFor(() => expect(onLearn).toHaveBeenCalledWith("cartly"));
  expect((await screen.findByRole("alert")).textContent).toMatch(/Couldn't start learning/);
});

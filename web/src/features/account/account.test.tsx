// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import type { Environment, LineAgreement, PlanStep, RunSummary } from "../../lib/schema";
import {
  ExplainSchema,
  LineAgreementSchema,
  PlanStepSchema,
  RunSummarySchema,
} from "../../lib/schema";
import { RunsView } from "../runs/RunsView";
import { Root } from "../shell/Root";
import { Sidebar } from "../shell/Sidebar";
import { HowDecided } from "../test/HowDecided";
import { Steps } from "../test/Steps";
import type { Account } from "./account";
import { WorkspaceSettings } from "./WorkspaceSettings";

// The page behind the account gate, reduced to one request, so the gate is what is tested.
vi.mock("../shell/App", () => ({
  App: () => {
    useEffect(() => {
      void api.targets().catch(() => null);
    }, []);
    return <p>page</p>;
  },
}));

type Answer = { status?: number; body?: unknown };
type Call = { method: string; path: string; headers: Record<string, string>; body: unknown };

const LOCAL = {
  hosted: false,
  user: null,
  workspace: { id: "local", name: "Local", personal: true },
  workspaces: [{ id: "local", name: "Local", personal: true }],
  model_key: { set: false, last4: null },
};
const ANA = { id: "u1", email: "ana@example.test", name: "Ana" };
const TEAM = { id: "w1", name: "Team", personal: false };
const HOSTED = { ...LOCAL, hosted: true, user: ANA, workspace: TEAM, workspaces: [TEAM] };

let calls: Call[] = [];

/** Answers each `METHOD /path` from `routes`; anything else is an empty list. */
function serve(routes: Record<string, Answer | (() => Answer)>) {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init: RequestInit = {}) => {
      const method = init.method ?? "GET";
      calls.push({
        method,
        path,
        headers: (init.headers ?? {}) as Record<string, string>,
        body: init.body ? JSON.parse(String(init.body)) : undefined,
      });
      const route = routes[`${method} ${path.split("?")[0]}`];
      const { status = 200, body = [] } = (typeof route === "function" ? route() : route) ?? {};
      return new Response(status === 204 ? null : JSON.stringify(body), {
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

describe("the page, local", () => {
  it("shows the page as it was: no sign-in, no workspace, no workspace header", async () => {
    serve({ "GET /v1/me": { body: LOCAL } });
    render(<Root />);
    await screen.findByText("page");
    await waitFor(() => expect(calls.some((call) => call.path === "/v1/me")).toBe(true));
    expect(screen.queryByText("Sign in")).toBeNull();
    expect(window.location.search).toBe("");
    expect(calls.every((call) => !("X-Workspace" in call.headers))).toBe(true);
  });

  it("keeps the product name in the sidebar, with no workspace switcher", () => {
    render(
      <Sidebar
        place={{ target: null, set: null, view: "home", run: null, case: null, base: null }}
        targets={[]}
        target={null}
        sets={[]}
        set={null}
        going={null}
        goingLabel=""
        asking={false}
        onAskAi={null}
        cards={null}
        onTarget={() => {}}
        onSet={() => {}}
        onView={() => {}}
        onGoing={() => {}}
      />,
    );
    expect(screen.getByText("evalution")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Workspace" })).toBeNull();
  });
});

describe("the page, hosted", () => {
  it("signed out, shows only sign-in, coming back to where the person was", async () => {
    window.history.replaceState(null, "", "/?view=runs");
    serve({ "GET /v1/me": { body: { ...HOSTED, user: null, workspace: null, workspaces: [] } } });
    render(<Root />);
    const link = await screen.findByRole("link", { name: "Sign in" });
    expect(link.getAttribute("href")).toBe(`/auth/login?next=${encodeURIComponent("/?view=runs")}`);
    expect(screen.queryByText("page")).toBeNull();
  });

  it("names the workspace on every request, and a 401 turns to sign-in", async () => {
    serve({ "GET /v1/me": { body: HOSTED }, "GET /v1/targets": { status: 401 } });
    render(<Root />);
    await screen.findByRole("link", { name: "Sign in" });
    await waitFor(() =>
      expect(new URLSearchParams(window.location.search).get("workspace")).toBe("w1"),
    );

    await act(() => api.tokens.list());
    const last = calls.at(-1);
    expect(last?.headers["X-Workspace"]).toBe("w1");
    expect(last?.headers["X-Requested-With"]).toBe("evalution");
  });

  it("shows a new token once, then only its prefix and scope", async () => {
    window.history.replaceState(null, "", "/?workspace=w1&view=model");
    let made = false;
    serve({
      "POST /v1/tokens": () => {
        made = true;
        return {
          body: {
            id: "t1",
            name: "app",
            prefix: "evt_ab12",
            scope: "traces",
            token: "evt_ab12secret",
          },
        };
      },
      "GET /v1/tokens": () => ({
        body: made
          ? [
              {
                id: "t1",
                name: "app",
                prefix: "evt_ab12",
                created_at: 1,
                last_used_at: null,
                scope: "traces",
              },
            ]
          : [],
      }),
    });
    const account: Account = {
      me: { ...HOSTED, user: ANA, workspace: TEAM },
      reload: async () => {},
    };
    const { unmount } = render(<WorkspaceSettings account={account} />);

    fireEvent.click(await screen.findByRole("button", { name: "New token" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Token name" }), {
      target: { value: "app" },
    });
    fireEvent.change(screen.getByRole("combobox", { name: "Token scope" }), {
      target: { value: "traces" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create" }));
    const shown = await screen.findByRole<HTMLInputElement>("textbox", { name: "New token" });
    expect(shown.value).toBe("evt_ab12secret");
    expect(calls.find((call) => call.method === "POST")?.body).toEqual({
      name: "app",
      scope: "traces",
    });
    await screen.findByText("evt_ab12…");
    screen.getByText("· Traces only");

    unmount();
    render(<WorkspaceSettings account={account} />);
    await screen.findByText("evt_ab12…");
    expect(screen.queryByDisplayValue("evt_ab12secret")).toBeNull();
  });
});

describe("runs from more than one place", () => {
  /** A finished run of one passing test, played where `environment` says. */
  const run = (id: string, at: number, environment?: Environment) =>
    RunSummarySchema.parse({
      run: id,
      set: "main",
      started_at: at,
      status: "done",
      environment,
      pass_marks: { tools: 0.8, reply: 0.7 },
      totals: {
        passed: 1,
        cases: 1,
        tools: null,
        reply: null,
        median_seconds: 1,
        usd: 0,
        tokens: 0,
      },
      tests: [{ case: "greets", name: "Greets", verdict: "pass", passed: 1, tries: 1, usd: 0 }],
    });
  const view = (runs: RunSummary[]) =>
    render(<RunsView runs={runs} label={(id) => id} empty={null} onOpen={() => {}} />);

  it("groups them by where they played, hosted first", () => {
    view([
      run("r3", 3, { where: "local", name: "staging" }),
      run("r2", 2, { where: "local", name: null }),
      run("r1", 1, { where: "hosted", name: null }),
    ]);
    const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(headings).toEqual(["Hosted", "Local", "staging"]);
  });

  it("shows no heading when every run played in one place, as runs stored before did", () => {
    view([run("r2", 2), run("r1", 1)]);
    expect(screen.queryAllByRole("heading", { level: 2 })).toEqual([]);
    expect(screen.getByText("r1")).toBeTruthy();
  });
});

describe("agreeing with the grader on a rubric line", () => {
  const LINE = "Greets back by name";
  const reply: PlanStep = PlanStepSchema.parse({
    kind: "reply",
    items: [
      {
        id: "rubric-0",
        text: LINE,
        mode: "must",
        judged: "graded",
        actual: { passed: true, text: "It did" },
      },
    ],
  });
  const said = (fields: Partial<LineAgreement>) =>
    LineAgreementSchema.parse({ agreed: 0, labelled: 0, unreliable: false, can: true, ...fields });

  function lines(agreement: LineAgreement, onLabel = vi.fn()) {
    render(
      <Steps
        steps={[reply]}
        names={{}}
        appName="the app"
        result
        agreement={{ "rubric-0": agreement }}
        onLabel={onLabel}
      />,
    );
    return onLabel;
  }

  it("takes one key on the focused line, or a button, and a second press clears it", () => {
    const onLabel = lines(said({ agreed: 1, labelled: 2, mine: false }));
    const line = screen.getByRole("group", { name: LINE });
    fireEvent.keyDown(line, { key: "a" });
    expect(onLabel).toHaveBeenLastCalledWith("rubric-0", true);
    fireEvent.keyDown(line, { key: "d" });
    expect(onLabel).toHaveBeenLastCalledWith("rubric-0", null);
    fireEvent.click(screen.getByRole("button", { name: "Agree" }));
    expect(onLabel).toHaveBeenLastCalledWith("rubric-0", true);
    expect(screen.getByRole("button", { name: "Disagree" }).getAttribute("aria-pressed")).toBe(
      "true",
    );
    expect(screen.getByText("1/2")).toBeTruthy();
  });

  it("marks a line the grader is unreliable on, and says so where the try was decided", () => {
    lines(said({ agreed: 1, labelled: 4, unreliable: true }));
    expect(screen.getByText("1/4 · grader unreliable here")).toBeTruthy();
    const explain = ExplainSchema.parse({
      code: { held: 0, total: 0, checks: [] },
      model: { held: 0, total: 1, unjudged: 0, summary: null, reference: null, cut: 0 },
      judge: null,
      rules: 2,
      rules_from: null,
    });
    render(<HowDecided explain={explain} doubts={[{ line: 1, disagreed: 3, labelled: 4 }]} />);
    expect(screen.getByText("Line 1: you disagreed 3 of 4 times")).toBeTruthy();
  });

  it("sends the answer for the try's line, and clears it", async () => {
    const answer = { body: { agreed: 0, labelled: 1, unreliable: false, mine: false, can: true } };
    const path = "/v1/evals/runs/r1/cases/greets/tries/2/rubric/0/label";
    serve({ [`PUT ${path}`]: answer, [`DELETE ${path}`]: answer });
    expect((await api.label("r1", "greets", 2, 0, false)).labelled).toBe(1);
    await api.label("r1", "greets", 2, 0, null);
    expect(calls.map((call) => [call.method, call.path, call.body])).toEqual([
      ["PUT", path, { agree: false }],
      ["DELETE", path, undefined],
    ]);
  });
});

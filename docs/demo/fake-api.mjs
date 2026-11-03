// A small stand-in for the evalution server: the routes of web/src/lib/api.ts over fictional data,
// and the built page. It spends nothing and calls no model. Work the real server does in the
// background (reading a folder, the test writer, a run) plays out on timers from when it was asked.

import { createReadStream, existsSync } from "node:fs";
import { createServer } from "node:http";
import { extname, join } from "node:path";
import * as cartly from "./cartly.mjs";

const { TARGET, SET, clock } = cartly;
const SEC = 1000;
const MIME = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" };
const MARKS = { tools: 0.8, reply: 0.7 };

const zero = { passed: 0, cases: 0, tools: 0, reply: 0, median_seconds: 0, usd: 0, tokens: 0 };

export function startFakeApi({ port, webRoot }) {
  const state = {
    connected: false,
    learned: false,
    job: null,
    session: null,
    tests: [],
    runs: [],
    live: null,
    codeChanged: false,
  };
  const later = (ms, fn) => setTimeout(fn, ms);

  // --- The test writer ---------------------------------------------------------------------

  const say = (reply, then) => {
    const session = state.session;
    session.status = "working";
    session.activity = [];
    const gap = 700;
    reply.activity.forEach((text, index) => {
      later(index * gap, () => session.activity.push({ at: clock.connect, text }));
    });
    later(reply.activity.length * gap + 300, () => {
      session.messages.push({ id: `m${session.messages.length}`, role: "agent", at: clock.connect, ...reply.message });
      session.spent_usd += 0.03;
      session.status = "idle";
      then?.();
    });
  };

  const send = (text) => {
    const session = state.session;
    session.messages.push({ id: `m${session.messages.length}`, role: "admin", at: clock.connect, text });
    if (/^Learn /.test(text)) say(cartly.REPLIES.learn, () => (state.learned = true));
    else if (/^About Run/.test(text)) say(cartly.REPLIES.why);
    else say(cartly.REPLIES.plan);
    return session;
  };

  // --- Runs --------------------------------------------------------------------------------

  const row = (test, run) => {
    const tries = (run?.tries ?? []).filter((entry) => entry.test.case === test.case);
    const passed = tries.filter((entry) => entry.passed).length;
    const verdict = !tries.length ? null : passed === tries.length ? "pass" : passed === 0 ? "fail" : "flaky";
    return {
      ...test,
      source: null,
      verdict,
      basis: passed ? "checked" : "none",
      tries: tries.map((entry) => (entry.passed ? "passed" : "failed")),
      reasons: tries.map((entry) => entry.reason ?? null),
      tools: tries.length ? (passed ? 1 : 0.5) : null,
      reply: tries.length ? (passed ? 0.95 : 0.25) : null,
      seconds: tries.length ? 6 : null,
      tokens: tries.length ? 2100 : null,
      usd: tries.length ? 0.021 * tries.length : null,
      last_run: tries.length ? run.id : null,
      last_at: tries.length ? run.at : null,
      stale: null,
    };
  };

  const lastRunOf = (test) => [...state.runs].reverse().find((run) => run.tries.some((entry) => entry.test.case === test.case));
  const rows = () => state.tests.map((test) => row(test, lastRunOf(test)));

  const summary = (run) => {
    const going = run.status === "running";
    const shown = run.tests.map((test) => row(test, run));
    const settled = shown.filter((entry) => entry.tries.length === run.triesEach);
    const done = run.tries.length;
    const next = going ? run.script[done] : null;
    return {
      run: run.id,
      target: TARGET,
      set: SET,
      started_at: run.at,
      status: run.status,
      pass_marks: MARKS,
      analyzing: run.analyzing,
      scope: "all",
      cases: run.tests.map((test) => test.case),
      tests: shown.map((entry) => ({
        case: entry.case,
        name: entry.name,
        verdict: entry.verdict,
        passed: entry.tries.filter((outcome) => outcome === "passed").length,
        tries: entry.tries.length,
        usd: entry.usd ?? 0,
      })),
      totals: {
        ...zero,
        passed: shown.filter((entry) => entry.verdict === "pass" && entry.tries.length === run.triesEach).length,
        cases: run.tests.length,
        median_seconds: done ? 6 : 0,
        usd: Number((done * 0.021).toFixed(3)),
        tokens: done * 2100,
      },
      progress: {
        done,
        total: run.script.length,
        tests_done: settled.length,
        tests_total: run.tests.length,
        current: next ? { case: next.test.case, name: next.test.name, try: Math.floor(done / run.tests.length) + 1 } : null,
      },
    };
  };

  const detail = (run) => ({
    ...summary(run),
    cases: run.tests.map((test) => row(test, run)),
    analysis: run.status === "done" && !run.analyzing ? (run.analysis ?? null) : null,
  });

  const startRun = () => {
    const run = {
      id: cartly.RUN_TWO,
      at: clock.secondRun,
      status: "running",
      analyzing: false,
      tests: state.tests,
      script: cartly.TRIES,
      triesEach: 3,
      tries: [],
      analysis: cartly.ANALYSIS,
    };
    state.runs.push(run);
    state.live = run;
    const each = 1.5 * SEC;
    run.script.forEach((entry, index) => later((index + 1) * each, () => run.tries.push(entry)));
    const end = (run.script.length + 1) * each;
    later(end, () => Object.assign(run, { status: "done", analyzing: true }));
    later(end + 3 * SEC, () => {
      run.analyzing = false;
      state.live = null;
    });
    return run;
  };

  /** What's on the screen before the story's ask: one test, run once. Connecting takes a while. */
  const seedFirstRun = () => {
    state.tests = [cartly.ORDER_TEST];
    state.runs = [
      {
        id: cartly.RUN_ONE,
        at: clock.firstRun,
        status: "done",
        analyzing: false,
        tests: [cartly.ORDER_TEST],
        script: cartly.TRIES.filter((entry) => entry.passed),
        triesEach: 3,
        tries: cartly.TRIES.filter((entry) => entry.passed),
      },
    ];
  };

  // --- Routes ------------------------------------------------------------------------------

  const understanding = () => {
    if (!state.learned) return cartly.NOT_LEARNED;
    const tests = state.tests.length;
    const passing = state.runs.length ? rows().filter((entry) => entry.verdict === "pass").length : null;
    const { card } = cartly.UNDERSTANDING;
    return { ...cartly.UNDERSTANDING, card: { ...card, numbers: { ...card.numbers, tests, tests_passing: passing } } };
  };

  const setSummary = () => ({
    set: SET,
    name: "Main",
    cases: state.tests.length,
    latest_run: state.runs.length ? summary(state.runs.at(-1)) : null,
    pass_marks: MARKS,
  });

  const health = () =>
    state.runs.length < 2
      ? {}
      : {
          ...cartly.HEALTH,
          // After a run the server looks for code changes: Home follows until it has looked.
          checking: !state.codeChanged,
          code_change: state.codeChanged ? cartly.CODE_CHANGE : null,
        };

  const discovery = () => {
    const { job } = state;
    const elapsed = Date.now() - job.at;
    const passed = cartly.DISCOVERY.filter((entry) => entry.at <= elapsed).at(-1);
    return {
      id: "folder-1",
      folder: job.folder,
      status: elapsed >= cartly.DISCOVERY.at(-1).at + 300 ? "ready" : "working",
      steps: passed?.steps ?? [{ text: "Reading cartly", state: "doing" }],
      questions: [],
      target: cartly.TARGET_DETAIL,
      traces: null,
    };
  };

  const routes = [
    ["GET", /^\/v1\/traces$/, () => ({ managed: true, url: "http://127.0.0.1:6006" })],
    ["GET", /^\/v1\/adapters$/, () => [cartly.ADAPTER]],
    ["GET", /^\/v1\/settings\/model$/, () => cartly.MODEL],
    ["GET", /^\/v1\/settings\/model\/models$/, () => ({ data: cartly.MODELS })],
    ["GET", /^\/v1\/library$/, () => cartly.LIBRARY],
    ["POST", /^\/v1\/targets\/cartly\/check$/, () => cartly.PROBE],
    ["GET", /^\/v1\/targets$/, async () => {
      // Lets "Ready" be read before the page moves on, as the real connect takes a moment here.
      if (state.connected && !state.learned) await new Promise((done) => later(1.2 * SEC, done));
      return state.connected ? [{ name: TARGET, adapter: "http", capabilities: ["reset", "state", "conversation_break"], managed: true, ...cartly.PROMISES }] : [];
    }],
    ["POST", /^\/v1\/targets\/discover$/, (body) => {
      state.job = { folder: body.path, at: Date.now() };
      return discovery();
    }],
    ["GET", /^\/v1\/targets\/discover\/[^/]+$/, () => discovery()],
    ["POST", /^\/v1\/targets\/discover\/[^/]+\/accept$/, () => {
      state.connected = true;
      return cartly.TARGET_DETAIL;
    }],
    ["GET", /^\/v1\/targets\/cartly$/, () => cartly.TARGET_DETAIL],
    ["GET", /^\/v1\/targets\/cartly\/sets$/, () => [setSummary()]],
    ["GET", /^\/v1\/targets\/cartly\/understanding$/, () => understanding()],
    ["GET", /^\/v1\/targets\/cartly\/health$/, () => health()],
    ["GET", /^\/v1\/evals\/runs\/[^/]+\/comparison$/, (_, [id]) => ({ run: id, base: null })],
    ["GET", /^\/v1\/evals\/sets\/main\/cases$/, () => rows()],
    ["GET", /^\/v1\/evals\/sets\/main\/cases\/[^/]+\/plan$/, () => cartly.SHOE_PLAN],
    ["GET", /^\/v1\/evals\/sets\/main\/cases\/([^/]+)$/, (_, [id]) => {
      const test = state.tests.find((entry) => entry.case === id);
      return { case: id, file: { name: test.name, about: test.about, turns: [] }, history: [] };
    }],
    ["GET", /^\/v1\/evals\/runs$/, () => state.runs.map(summary)],
    ["POST", /^\/v1\/evals\/runs\/estimate$/, (body) => ({
      token: "estimate-1",
      cases: body.cases?.length ?? state.tests.length,
      tries: 3,
      chats: 3 * (body.cases?.length ?? state.tests.length),
      usd: cartly.ESTIMATE_USD[body.cases?.length ?? state.tests.length],
      first_run: !state.runs.some((run) => run.id === cartly.RUN_TWO),
    })],
    ["POST", /^\/v1\/evals\/runs$/, () => summary(startRun())],
    ["GET", /^\/v1\/evals\/runs\/([^/]+)$/, (_, [id]) => detail(state.runs.find((run) => run.id === id))],
    ["POST", /^\/v1\/evals\/agent\/sessions$/, (body) => {
      state.session = { id: "writer-1", target: body.target, set: body.set, status: "idle", spent_usd: 0, messages: [], activity: [] };
      return state.session;
    }],
    ["GET", /^\/v1\/evals\/agent\/sessions\/[^/]+$/, () => state.session],
    ["POST", /^\/v1\/evals\/agent\/sessions\/[^/]+\/messages$/, (body) => send(body.text)],
    ["POST", /^\/v1\/evals\/agent\/sessions\/[^/]+\/approve$/, () => {
      const { session } = state;
      session.messages.push({ id: `m${session.messages.length}`, role: "admin", at: clock.connect, text: "Add to tests", approves: "plan-shoe-size" });
      say(cartly.REPLIES.add);
      later(0, () => state.tests.push(cartly.SHOE_TEST));
      return session;
    }],
  ];

  const server = createServer(async (request, response) => {
    const { pathname } = new URL(request.url, "http://demo");
    if (!pathname.startsWith("/v1/")) {
      const file = join(webRoot, pathname === "/" ? "index.html" : pathname);
      if (!existsSync(file)) return void response.writeHead(404).end();
      response.writeHead(200, { "Content-Type": MIME[extname(file)] ?? "application/octet-stream" });
      return void createReadStream(file).pipe(response);
    }
    let raw = "";
    for await (const chunk of request) raw += chunk;
    for (const [method, pattern, handle] of routes) {
      const match = pattern.exec(pathname);
      if (match && method === request.method) {
        const answer = await handle(raw ? JSON.parse(raw) : {}, match.slice(1).map(decodeURIComponent));
        response.writeHead(200, { "Content-Type": "application/json" });
        return void response.end(JSON.stringify(answer));
      }
    }
    response.writeHead(404, { "Content-Type": "application/json" });
    response.end(JSON.stringify({ detail: `The demo has no ${request.method} ${pathname}` }));
  });

  return new Promise((resolve) => {
    server.listen(port, "127.0.0.1", () =>
      resolve({
        seedFirstRun,
        codeChanged: () => (state.codeChanged = true),
        close: () => {
          server.closeAllConnections();
          server.close();
        },
      }),
    );
  });
}

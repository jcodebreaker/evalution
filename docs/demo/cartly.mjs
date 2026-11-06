// Everything the demo shows about Cartly, a made-up shopping assistant. All names and numbers are
// fictional. Shapes follow web/src/lib/schema.ts; the app parses them with those same schemas.

export const TARGET = "cartly";
export const SET = "main";
export const FOLDER = "~/projects/cartly";

/** The demo's clock, in seconds: Tuesday, October 27, 2:05 PM in New York. */
const start = Date.UTC(2026, 9, 27, 18, 5) / 1000;
export const clock = {
  connect: start,
  firstRun: start + 15 * 60,
  tests: start + 26 * 60,
  secondRun: start + 28 * 60,
  codeChange: start + 65 * 60,
};

export const DISCOVERY = [
  [600, ["Reading cartly", "done"], ["Looking for how to chat with it", "doing"]],
  [1200, ["Reading cartly", "done"], ["Found where it takes chat messages (/api/chat)", "done"], ["Checking that your app is running", "doing"]],
  [1800, ["Reading cartly", "done"], ["Found where it takes chat messages (/api/chat)", "done"], ["Your app is running", "done"], ["Looking for conversations", "doing"]],
  [2400, ["Reading cartly", "done"], ["Found where it takes chat messages (/api/chat)", "done"], ["Your app is running", "done"], ["Found 38 recent conversations", "done"], ["Letting the agent read your code", "doing"]],
  [3000, ["Reading cartly", "done"], ["Found where it takes chat messages (/api/chat)", "done"], ["Your app is running", "done"], ["Found 38 recent conversations", "done"], ["The agent can read 64 files of your code", "done"]],
].map(([at, ...steps]) => ({ at, steps: steps.map(([text, state]) => ({ text, state })) }));

export const ADAPTER = {
  name: "http",
  label: "HTTP adapter",
  options: ["send", "reply", "done"],
  required: ["send", "reply", "done"],
  secrets: ["authorization"],
};

export const MODELS = [
  { id: "big-model", name: "Big model" },
  { id: "small-model", name: "Small model" },
];

export const MODEL = {
  provider: "compatible",
  base_url: "http://127.0.0.1:11434/v1",
  strong: "big-model",
  light: "small-model",
  key_set: true,
  source: "page",
};

export const PROMISES = {
  isolation_note: "Each try gets a new session id; what carries over is up to your app.",
  starts_clean: ["conversation"],
  required_clean: [],
  checked: false,
};

export const PROBE = {
  ok: true,
  reply: "OK",
  seconds: 1.4,
  tools: 0,
  spans: 3,
  traces: "complete",
  problems: [],
  next: null,
  can_redraft: false,
};

const given = [
  { name: "prompt", label: "What the person sends" },
  { name: "outcome", label: "What should happen" },
  { name: "must_not", label: "What must not happen" },
];
const fits = { verdict: "fits", label: "Fits", reason: "" };
export const LIBRARY = [
  { id: "exact-answer", title: "Exact answer", checks: "", kind: "exact", asks: given, fit: fits },
  { id: "tool-use", title: "Tool use", checks: "", kind: "tools", asks: given, fit: fits },
  {
    id: "background-work",
    title: "Background work",
    checks: "",
    kind: "delegation",
    asks: given,
    fit: { verdict: "needs", label: "Needs waiting on background work", reason: "" },
  },
  {
    id: "memory-check",
    title: "Memory",
    checks: "",
    kind: "memory",
    asks: given,
    fit: { verdict: "needs", label: "Needs a clean start of memory", reason: "" },
  },
];

export const TARGET_DETAIL = {
  name: TARGET,
  adapter: "http",
  adapter_label: "HTTP adapter",
  options: {
    send: { method: "POST", url: "http://127.0.0.1:3000/api/chat", body: { message: "{message}", session: "{session}" } },
    reply: "$.reply",
    done: "response",
  },
  phoenix: { url: "http://127.0.0.1:6006", project: "cartly" },
  code: { path: FOLDER, ignore: [] },
  secrets: [],
  managed: true,
};

const card = {
  name: "Cartly",
  one_line: "A shopping assistant that finds products, tracks orders and remembers customers",
  stack: ["TypeScript", "Next.js", "Postgres", "OpenTelemetry"],
  status: "up_to_date",
  changes: 0,
  numbers: {
    tools: 6,
    tools_seen: 5,
    state_kinds: ["profile", "notes", "cart"],
    ask_seconds: 6,
    tests: 0,
    tests_passing: null,
    library_covered: 0,
    library_total: 10,
  },
  watched: ["agent/", "app/api/"],
};

export const NOT_LEARNED = {
  version: 0,
  card: { ...card, one_line: "", stack: [], status: "learning", numbers: { ...card.numbers, tools: 0, tools_seen: 0, state_kinds: [], ask_seconds: null } },
};

const code = (ref) => ({ kind: "code", ref });
const traces = (ref) => ({ kind: "traces", ref });

export const UNDERSTANDING = {
  version: 1,
  written_at: clock.connect,
  fingerprint: "c4r7",
  card,
  diagram: {
    nodes: [
      { id: "shopper", kind: "person", title: "Shopper", sub: "Asks in chat", evidence: [traces("38 conversations")] },
      { id: "agent", kind: "agent", title: "Cartly agent", sub: "Plans and replies", evidence: [code("agent/graph.ts")] },
      { id: "catalog", kind: "tools", title: "Catalog search", sub: "search_products", evidence: [code("agent/tools/catalog.ts")] },
      { id: "orders", kind: "tools", title: "Order lookup", sub: "get_order", evidence: [code("agent/tools/orders.ts")] },
      { id: "memory", kind: "tools", title: "Memory tools", sub: "save_profile, add_note", evidence: [code("agent/tools/profile.ts"), code("agent/tools/notes.ts")] },
      { id: "returns", kind: "worker", title: "Returns desk", sub: "Handles return requests", seen: false, evidence: [code("agent/returns.ts")] },
      { id: "cart", kind: "state", title: "Cart", sub: "One per customer", evidence: [code("agent/tools/cart.ts")] },
      { id: "customer", kind: "state", title: "Customer profile", sub: "Kept for good", evidence: [code("agent/tools/profile.ts")] },
      { id: "notes", kind: "state", title: "Conversation notes", sub: "Gone when the chat ends", evidence: [code("agent/tools/notes.ts")] },
    ],
    edges: [
      { from: "shopper", to: "agent", asks: 38 },
      { from: "agent", to: "catalog", asks: 24 },
      { from: "agent", to: "orders", asks: 11 },
      { from: "agent", to: "memory", asks: 14 },
      { from: "agent", to: "returns", asks: 0, seen: false },
      { from: "catalog", to: "cart", asks: 9 },
      { from: "memory", to: "customer", asks: 2 },
      { from: "memory", to: "notes", asks: 12 },
    ],
  },
  paths: [
    { share: 0.58, steps: ["Search the catalog", "Reply"], seen: true },
    { share: 0.26, steps: ["Look up the order", "Reply"], seen: true },
    { share: 0.16, steps: ["Read the profile", "Search the catalog", "Add to cart", "Reply"], seen: true },
    { share: 0, steps: ["Hand off to the returns desk", "Reply"], seen: false },
  ],
  sections: [
    {
      id: "memory",
      title: "What it keeps",
      claims: [
        { id: "c1", text: "Facts about a customer belong in their profile and last across conversations.", evidence: [code("agent/tools/profile.ts")] },
        { id: "c2", text: "Notes are kept for one conversation only.", evidence: [code("agent/tools/notes.ts")] },
      ],
    },
  ],
  priorities: [
    { id: "orders", title: "Finding the right order", picked: true },
    { id: "remember", title: "Remembering customers", picked: true },
    { id: "returns", title: "Returns", picked: false },
  ],
  starters: [
    { id: "s1", title: "Finds the right order", what: "Asked about the latest order, it looks it up and quotes the real date.", why: "11 recent conversations ask this", priority: "orders", selected: true },
    { id: "s2", title: "A preference carries over to a new conversation", what: "A standing preference still applies in the next chat.", why: "Having to repeat yourself is what people notice first", priority: "remember", library: "preference-across-conversations", selected: false },
  ],
};

export const ORDER_TEST = {
  case: "finds-the-right-order",
  name: "Finds the right order",
  about: "Asked about the latest order, it looks it up and quotes the real delivery date.",
};

export const SHOE_TEST = {
  case: "remembers-shoe-size",
  name: "Remembers a customer's shoe size",
  about: "A size given in one conversation is known in the next one.",
};

const SHOE_REASON = "save_profile was never called, so the new conversation asked for the size again";
const SHOE_FINDING = "The size goes to conversation notes, so a new conversation doesn't have it";

export const REPLIES = {
  learn: {
    activity: ["Looking at the code", "Reading recent traces", "Reading agent/graph.ts", "Reading agent/tools/profile.ts", "Saving what I understood"],
    message: {
      read: ["Read agent/graph.ts", "Read agent/tools/profile.ts"],
      text: "I've learned Cartly, a shopping assistant. Check what I understood.",
    },
  },
  plan: {
    activity: ["Reading what I understood", "Reading agent/tools/profile.ts", "Looking at the test library", "Adapting the library test preference-across-conversations"],
    message: {
      read: ["Read agent/tools/profile.ts", "Read agent/tools/notes.ts"],
      text: "Cartly can keep a fact in the customer's profile or in that conversation's notes. This checks the size lands where a new conversation can find it.",
      plan: {
        id: "plan-shoe-size",
        case: SHOE_TEST.case,
        tries: 3,
        steps: [
          { kind: "message", title: "Give the size", note: "“I wear a 9.5 in running shoes. Any trail runners under $120?”" },
          { kind: "check", title: "The size is saved to the profile" },
          { kind: "message", title: "Start a new conversation", note: "“Do the Ridgeline Trail 2 come in my size?”" },
          { kind: "check", title: "It answers for a 9.5 without asking" },
        ],
      },
    },
  },
  add: {
    activity: ["Adding the test"],
    message: { text: "Added. It's ready to run.", saved: SHOE_TEST.case },
  },
  why: {
    activity: ["Reading the results of Run 2", "Reading try 1 of remembers-shoe-size", "Reading agent/prompts/system.md"],
    message: {
      read: ["Read try 1 of remembers-shoe-size", "Read agent/prompts/system.md"],
      text: "",
      review: {
        summary: "Because the prompt sends it to notes. It says to “jot down what the customer mentions”, and nothing says a size should last, so it never reaches for the profile.",
        findings: [
          {
            kind: "assistant",
            text: "All 3 tries called `add_note` with the size and never `save_profile`. Notes end with the conversation, so the next one had to ask.",
            case: SHOE_TEST.case,
            evidence: ["add_note(\"wears a 9.5 in running shoes\")", "read_profile → { size: null }", "“What size do you wear?”"],
          },
          {
            kind: "assistant",
            text: "Add a line to `agent/prompts/system.md`: save sizes and other lasting facts with `save_profile`. Then run the test again.",
          },
        ],
      },
    },
  },
};

/** The test page's steps, before it has run. */
export const SHOE_PLAN = {
  name: SHOE_TEST.name,
  about: SHOE_TEST.about,
  limits: ["3 tries", "120 s per try"],
  names: { read_profile: "Read the profile", save_profile: "Save to the profile", add_note: "Add a note" },
  steps: [
    { kind: "before", title: "A fresh person" },
    { kind: "say", title: "The customer gives their size", message: "I wear a 9.5 in running shoes. Any trail runners under $120?" },
    {
      kind: "state",
      title: "The app's state is checked",
      items: [{ id: "s1", text: "The customer's profile holds shoe size 9.5", mode: "must", judged: "exact" }],
    },
    { kind: "break", title: "A new conversation" },
    { kind: "say", title: "They ask about a pair", message: "Do the Ridgeline Trail 2 come in my size?" },
    {
      kind: "agent",
      items: [
        { id: "a1", text: "Reads the customer's profile", detail: "read_profile", mode: "must", judged: "exact" },
        { id: "a2", text: "Asks the catalog for size 9.5", detail: "search_products · 9.5", mode: "must", judged: "exact" },
        { id: "a3", text: "In any order; other steps are fine", mode: "allowed", judged: "exact" },
      ],
    },
    {
      kind: "reply",
      reference: "Yes, the Ridgeline Trail 2 comes in a 9.5, and it's in stock.",
      items: [
        { id: "r1", text: "Answers for a size 9.5 without asking for the size", mode: "must", judged: "graded" },
        { id: "r2", text: "Says whether the Ridgeline Trail 2 comes in that size", mode: "must", judged: "graded" },
      ],
    },
    {
      kind: "passes",
      title: "Every check above holds, on all 3 tries",
      note: "One failed try makes it flaky; two or more make it fail. At least 80% of the expected steps must happen.",
    },
  ],
};

/** Run 1: the first test alone. Run 2: both, one try finishing at a time. */
export const RUN_ONE = "20261027-182000-4e1b";
export const RUN_TWO = "20261027-183300-7c2e";

export const TRIES = [
  { test: ORDER_TEST, passed: true },
  { test: SHOE_TEST, passed: false, reason: SHOE_REASON },
  { test: ORDER_TEST, passed: true },
  { test: SHOE_TEST, passed: false, reason: SHOE_REASON },
  { test: ORDER_TEST, passed: true },
  { test: SHOE_TEST, passed: false, reason: SHOE_REASON },
];

const claim = (text) => ({ text, refs: [], quotes: [], verified: true });

export const ANALYSIS = {
  summary: claim(SHOE_FINDING),
  patterns: [{ title: SHOE_FINDING, detail: claim(SHOE_REASON), cases: [SHOE_TEST.case], suspected: "app" }],
};

export const REPORT = {
  run: RUN_TWO,
  facts: {
    passed: 1,
    total: 2,
    model_only: 0,
    held: [{ case: ORDER_TEST.case, name: ORDER_TEST.name, passed: 3, judged: 3, basis: "checked" }],
    failed: [{ case: SHOE_TEST.case, name: SHOE_TEST.name, verdict: "fail", reason: SHOE_REASON }],
    unjudged: [],
    regressions: [],
  },
  well: [
    { ...claim("It looked up the right order and quoted the real delivery date in all 3 tries."), case: ORDER_TEST.case },
  ],
  reading: [
    {
      ...claim("The shoe size is lost: it's saved to conversation notes instead of the customer's profile, so a new conversation asks again."),
      case: SHOE_TEST.case,
    },
  ],
  next: [
    { ...claim("Tell the agent in agent/prompts/system.md to save sizes with save_profile."), case: SHOE_TEST.case },
    { text: "Run “Remembers a customer's shoe size” again to confirm it.", case: SHOE_TEST.case },
  ],
};

/** Home after Run 2. */
export const HEALTH = {
  tiles: {
    passing: { value: 1, total: 2, delta: 0 },
    consistent: { value: 2, total: 2 },
    reply_seconds: { value: 6, delta: null },
  },
  areas: [
    { name: "Finding the right order", pct: 100, level: "strong" },
    { name: "Remembering customers", pct: 0, level: "weak" },
  ],
  trend: [
    { run: RUN_ONE, at: clock.firstRun, passing: 1, total: 1 },
    { run: RUN_TWO, at: clock.secondRun, passing: 1, total: 2 },
  ],
  attention: [
    { title: `${SHOE_TEST.name} fails`, detail: SHOE_FINDING, action: "see_why", case: SHOE_TEST.case, run: RUN_TWO },
  ],
  recent: [
    { run: RUN_TWO, name: "All tests", at: clock.secondRun, results: ["pass", "fail"] },
    { run: RUN_ONE, name: "All tests", at: clock.firstRun, results: ["pass"] },
  ],
  report: REPORT,
};

export const CODE_CHANGE = {
  summary: "Order history now reads sizes from past orders, and returns take a reason.",
  areas: ["order history", "returns"],
  uncovered: ["returns"],
  files: ["agent/tools/orders.ts", "agent/returns.ts", "app/api/orders/route.ts"],
  file_count: 3,
  tests: [
    { set: SET, case: ORDER_TEST.case, name: ORDER_TEST.name },
    { set: SET, case: SHOE_TEST.case, name: SHOE_TEST.name },
  ],
};

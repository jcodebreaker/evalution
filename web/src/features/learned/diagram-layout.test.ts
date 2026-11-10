import { describe, expect, it } from "vitest";
import { type DiagramNode, DiagramNodeSchema, UnderstandingSchema } from "../../lib/schema";
import { type Diagram, foldersOf, type Item, MOST, OTHER, type Plan, plan } from "./diagram-layout";

type Extra = Partial<Omit<DiagramNode, "id" | "kind">> & { ref?: string };

const node = (id: string, kind: DiagramNode["kind"], extra: Extra = {}) => {
  const { ref, ...rest } = extra;
  return DiagramNodeSchema.parse({
    id,
    kind,
    title: rest.title ?? id,
    evidence: ref ? [{ kind: "code", ref }] : [],
    ...rest,
  });
};

/** The person, the main agent, and `others`, each reached by the main agent `asks[id]` times. */
function diagram(others: DiagramNode[], asks: Record<string, number> = {}): Diagram {
  const nodes = [
    node("person", "person"),
    node("main", "agent", { title: "Main agent" }),
    ...others,
  ];
  const edges = [
    { from: "person", to: "main", asks: 50, seen: true },
    ...others.map((other) => ({
      from: "main",
      to: other.id,
      asks: asks[other.id] ?? 0,
      seen: (asks[other.id] ?? 0) > 0,
    })),
  ];
  return UnderstandingSchema.shape.diagram.parse({ nodes, edges });
}

const column = (result: Plan, at: number) => result.columns[at] ?? [];
const names = (items: Item[]) =>
  items.map((item) =>
    "group" in item ? `${item.group.name} · ${item.group.members.length}` : item.id,
  );
const groupNamed = (result: Plan, name: string) =>
  result.columns
    .flat()
    .flatMap((item) => ("group" in item ? [item.group] : []))
    .find((group) => group.name === name);

/** 110 more agents: 80 examples, 20 core, 10 labs; 16 of them seen, spread over the folders. */
function platform() {
  const asks: Record<string, number> = {};
  const others: DiagramNode[] = [];
  const folder = (n: number) => (n < 80 ? "examples" : n < 100 ? "core" : "labs");
  for (let n = 0; n < 110; n++) {
    const seen = n % 7 === 0;
    const id = `agent${n}`;
    if (seen) asks[id] = 120 - n;
    others.push(
      node(id, "agent", {
        title: `Agent ${n}`,
        seen,
        ref: `agents/${folder(n)}/a${n}/agent.py:3`,
      }),
    );
  }
  return { diagram: diagram(others, asks), asks };
}

describe("foldersOf", () => {
  it("reads the folders of the file a node's code names", () => {
    expect(foldersOf(node("a", "agent", { ref: "agents/examples/triage/agent.py:12" }))).toEqual([
      "agents",
      "examples",
      "triage",
    ]);
    expect(foldersOf(node("a", "agent", { ref: '`./tools/orders.ts` "refund"' }))).toEqual([
      "tools",
    ]);
    expect(foldersOf(node("a", "agent", { ref: "AGENTS.md" }))).toEqual([]);
    expect(foldersOf(node("a", "agent"))).toEqual([]);
  });
});

describe("plan", () => {
  it("draws a small diagram whole: no groups", () => {
    const tools = ["search", "orders", "memory", "returns"].map((id) => node(id, "tools"));
    const state = ["cart", "profile"].map((id) => node(id, "state"));
    const result = plan(diagram([...tools, ...state], { search: 24, orders: 11, memory: 14 }));
    expect(result.columns.flat().every((item) => "node" in item)).toBe(true);
    expect(result.columns.flat()).toHaveLength(8);
    // Seen first, by asks; then the rest by title.
    expect(names(column(result, 2))).toEqual(["search", "memory", "orders", "returns"]);
    expect(result.edges).toHaveLength(7);
  });

  it("draws 8 agent boxes of 111: the seen ones first, the rest in their folders", () => {
    const { diagram: big } = platform();
    const result = plan(big);
    const agents = column(result, 1);
    expect(agents).toHaveLength(MOST);
    const shown = agents.filter((item) => "node" in item);
    const groups = agents.flatMap((item) => ("group" in item ? [item.group] : []));
    expect(shown.length).toBeGreaterThan(0);
    expect(shown.every((item) => "node" in item && item.node.seen)).toBe(true);
    expect(shown[0]?.id).toBe("main");
    // In ask order: main (touching the most), then agent0 (120 asks), agent7 (113)...
    expect(shown.map((item) => item.id).slice(1, 3)).toEqual(["agent0", "agent7"]);
    expect(groups.map((group) => group.name)).toEqual([
      "agents/examples",
      "agents/core",
      "agents/labs",
    ]);
    const folded = groups.reduce((sum, group) => sum + group.members.length, 0);
    expect(shown.length + folded).toBe(111);
    // A group holding a seen member is solid; one with none is dashed.
    expect(groups.every((group) => group.seen)).toBe(true);
  });

  it("puts the summed asks of a group's members on its edge", () => {
    const { diagram: big } = platform();
    const result = plan(big);
    const core = groupNamed(result, "agents/core");
    expect(core).toBeDefined();
    const edge = result.edges.find((entry) => entry.to === core?.id);
    const expected = big.edges
      .filter((entry) => core?.members.some((member) => member.id === entry.to))
      .reduce((sum, entry) => sum + entry.asks, 0);
    expect(expected).toBeGreaterThan(0);
    expect(edge).toEqual({
      from: "main",
      to: core?.id,
      asks: expected,
      seen: true,
    });
    // One edge per pair, and only between drawn boxes.
    const drawn = new Set(result.columns.flat().map((item) => item.id));
    expect(result.edges.every((entry) => drawn.has(entry.from) && drawn.has(entry.to))).toBe(true);
    expect(result.edges.length).toBeLessThanOrEqual(1 + 7 + 0);
  });

  it("folds every never-seen box into a group, even with room to spare", () => {
    const examples = Array.from({ length: 40 }, (_, n) =>
      node(`ex${n}`, "agent", {
        seen: false,
        ref: `agents/examples/ex${n}.py`,
      }),
    );
    expect(names(column(plan(diagram(examples)), 1))).toEqual(["main", "agents/examples · 40"]);
    // A few with room for each: still grouped by folder, and a group of one is its own box.
    const tools = [
      node("search", "tools", { ref: "tools/search.py" }),
      node("refund", "tools", { seen: false, ref: "tools/pay/refund.py" }),
      node("charge", "tools", { seen: false, ref: "tools/pay/charge.py" }),
      node("notes", "tools", { seen: false, ref: "tools/notes/notes.py" }),
    ];
    const result = plan(diagram(tools, { search: 9 }));
    expect(names(column(result, 2))).toEqual(["search", "notes", "tools/pay · 2"]);
    expect(groupNamed(result, "tools/pay")?.seen).toBe(false);
  });

  it("draws a column with nothing seen box by box while there is room", () => {
    const state = Array.from({ length: 8 }, (_, n) =>
      node(`s${n}`, "state", { seen: false, ref: `store/s${n}.py` }),
    );
    const items = column(plan(diagram(state)), 3);
    expect(items).toHaveLength(8);
    expect(items.every((item) => "node" in item)).toBe(true);
  });

  it("merges the smallest folders into Other when there are too many", () => {
    const tools: DiagramNode[] = [];
    for (let f = 0; f < 12; f++) {
      for (let n = 0; n < (f < 2 ? 4 : 3); n++) {
        tools.push(
          node(`t${f}-${n}`, "tools", {
            seen: false,
            ref: `pkg${f}/tool${n}.py`,
          }),
        );
      }
    }
    const result = plan(diagram(tools));
    const items = column(result, 2);
    expect(items).toHaveLength(MOST);
    const last = items.at(-1);
    expect(last && "group" in last ? last.group.name : null).toBe(OTHER);
    expect(items.every((item) => "group" in item)).toBe(true);
    // The two biggest folders keep their own box.
    expect(names(items).slice(0, 2)).toEqual(["pkg0 · 4", "pkg1 · 4"]);
    const total = items.reduce(
      (sum, item) => sum + ("group" in item ? item.group.members.length : 1),
      0,
    );
    expect(total).toBe(38);
    // Nothing in it was ever seen: dashed.
    expect(items.every((item) => "group" in item && !item.group.seen)).toBe(true);
  });

  it("marks a group holding a flagged or wrong member", () => {
    const tools: DiagramNode[] = [];
    for (let n = 0; n < 20; n++) {
      tools.push(
        node(`t${n}`, "tools", {
          seen: false,
          ref: `${n < 10 ? "a" : "b"}/x/t${n}.py`,
          flagged: n === 3 ? true : undefined,
          verdict: n === 15 ? "wrong" : undefined,
        }),
      );
    }
    const result = plan(diagram(tools));
    expect(groupNamed(result, "a")?.mark ?? groupNamed(result, "a/x")?.mark).toBe("flagged");
    expect(groupNamed(result, "b")?.mark ?? groupNamed(result, "b/x")?.mark).toBe("wrong");
  });

  it("opens a group in place and pages through its members", () => {
    const { diagram: big } = platform();
    const examples = groupNamed(plan(big), "agents/examples");
    expect(examples).toBeDefined();
    const size = examples?.members.length ?? 0;
    const first = plan(big, { open: examples?.id });
    const agents = column(first, 1);
    expect(agents).toHaveLength(MOST);
    // The main agent stays so its edges have somewhere to start; the members fill the rest.
    expect(agents[0]?.id).toBe("main");
    expect(first.opened).toMatchObject({ page: 0, before: 0, after: size - 7 });
    expect(agents.slice(1).map((item) => item.id)).toEqual(
      examples?.members.slice(0, 7).map((member) => member.id),
    );
    const second = plan(big, { open: examples?.id, page: 1 });
    expect(
      column(second, 1)
        .slice(1)
        .map((item) => item.id),
    ).toEqual(examples?.members.slice(7, 14).map((member) => member.id));
    expect(second.opened?.before).toBe(7);
    // Each drawn member gets its own edge.
    expect(second.edges.filter((edge) => edge.from === "main")).toHaveLength(7);
  });

  it("finds a folded box by its title and shows it with what leads to it", () => {
    const { diagram: big } = platform();
    expect(column(plan(big), 1).some((item) => item.id === "agent61")).toBe(false);
    const result = plan(big, { query: "agent 61" });
    expect(result.matches).toBe(1);
    expect(names(column(result, 1))).toEqual(["main", "agent61"]);
    expect(result.edges).toEqual([{ from: "main", to: "agent61", asks: 0, seen: false }]);
    // By a file it comes from too.
    const core = plan(big, { query: "agents/core" });
    expect(core.matches).toBe(20);
    expect(column(core, 1)).toHaveLength(MOST);
  });

  it("finds a box by a tool name it stands for", () => {
    const tools = [
      node("orders", "tools", {
        title: "Orders",
        tools: ["get_order", "cancel_order"],
      }),
      node("search", "tools", { title: "Catalog" }),
    ];
    const result = plan(diagram(tools), { query: "Cancel_Order" });
    expect(result.matches).toBe(1);
    expect(names(column(result, 2))).toEqual(["orders"]);
  });
});

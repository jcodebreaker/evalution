// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { UnderstandingSchema } from "../../lib/schema";
import { DiagramView } from "./Diagram";

afterEach(cleanup);

/** The person, the main agent, two seen tools and `many` example agents never seen. */
function diagram(many: number) {
  const examples = Array.from({ length: many }, (_, n) => ({
    id: `ex${n}`,
    kind: "agent",
    title: `Example ${String(n).padStart(2, "0")}`,
    seen: false,
    evidence: [{ kind: "code", ref: `agents/examples/ex${n}.py` }],
    flagged: n === 30 ? true : undefined,
  }));
  return UnderstandingSchema.shape.diagram.parse({
    nodes: [
      { id: "person", kind: "person", title: "Shopper" },
      { id: "main", kind: "agent", title: "Cartly agent" },
      {
        id: "search",
        kind: "tools",
        title: "Catalog",
        sub: "search_products",
        tools: ["search_products", "lookup_sku"],
      },
      {
        id: "orders",
        kind: "tools",
        title: "Orders",
        sub: "get_order",
        verdict: "wrong",
      },
      ...examples,
    ],
    edges: [
      { from: "person", to: "main", asks: 38 },
      { from: "main", to: "search", asks: 24 },
      { from: "main", to: "orders", asks: 11 },
      ...examples.map((node) => ({
        from: "main",
        to: node.id,
        asks: 0,
        seen: false,
      })),
    ],
  });
}

const view = (many: number, onSelect = vi.fn()) =>
  render(<DiagramView diagram={diagram(many)} name="cartly" selected="main" onSelect={onSelect} />);

const picture = () => screen.getByRole("img", { name: "Diagram of how cartly works" });

it("draws a small diagram with no search, its never-seen boxes in a group", () => {
  view(3);
  expect(screen.queryByRole("searchbox")).toBeNull();
  // Shopper; main and the group of three examples; the two tools.
  expect(within(picture()).getAllByRole("button")).toHaveLength(5);
  expect(within(picture()).getByRole("button", { name: "agents/examples, 3 boxes" })).toBeDefined();
  expect(
    within(picture()).getByRole("button", { name: "Cartly agent" }).getAttribute("aria-pressed"),
  ).toBe("true");
});

it("folds many agents into a group box that opens in place, pages, and goes back", () => {
  const onSelect = vi.fn();
  view(40, onSelect);
  const boxes = within(picture()).getAllByRole("button");
  // Shopper; main and the group of every example, though there is room; the two tools.
  expect(boxes).toHaveLength(5);
  const group = within(picture()).getByRole("button", {
    name: "agents/examples, 40 boxes, one flagged",
  });
  // A group box is reached by the keyboard like any box.
  fireEvent.keyDown(group, { key: "Enter" });
  const members = within(picture())
    .getAllByRole("button")
    .map((box) => box.getAttribute("aria-label"));
  expect(members).toContain("Example 00");
  expect(members).toContain("Example 06");
  expect(members).not.toContain("Example 07");
  // The main agent stays beside them, so does what it calls.
  expect(members).toContain("Cartly agent");
  fireEvent.click(screen.getByRole("button", { name: "Show 33 more" }));
  expect(within(picture()).getByRole("button", { name: "Example 07" })).toBeDefined();
  fireEvent.click(within(picture()).getByRole("button", { name: "Example 08" }));
  expect(onSelect).toHaveBeenCalledWith("ex8");
  fireEvent.click(screen.getByRole("button", { name: "Back to all" }));
  expect(within(picture()).getAllByRole("button")).toHaveLength(5);
});

it("finds a folded box by name", () => {
  view(40);
  const search = screen.getByRole("searchbox", { name: "Find in the diagram" });
  fireEvent.change(search, { target: { value: "example 31" } });
  const shown = within(picture())
    .getAllByRole("button")
    .map((box) => box.getAttribute("aria-label"));
  expect(shown).toEqual(["Cartly agent", "Example 31"]);
  fireEvent.change(search, { target: { value: "get_order" } });
  expect(within(picture()).getByRole("button", { name: "Orders" })).toBeDefined();
  // By a tool name the box stands for.
  fireEvent.change(search, { target: { value: "lookup_sku" } });
  expect(
    within(picture())
      .getAllByRole("button")
      .map((box) => box.getAttribute("aria-label")),
  ).toEqual(["Cartly agent", "Catalog"]);
  fireEvent.change(search, { target: { value: "nothing like it" } });
  expect(screen.getByText("Nothing matches “nothing like it”")).toBeDefined();
});

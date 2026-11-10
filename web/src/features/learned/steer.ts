import type { Steer, Understanding } from "../../lib/schema";

/** The understanding with one steer applied, as the page shows it before the server answers. */
export function apply<
  U extends Pick<Understanding, "sections" | "diagram" | "starters" | "priorities">,
>(now: U, steer: Steer): U {
  const mark = <T extends { id: string; verdict?: "right" | "wrong"; fix?: string }>(
    item: T,
    id: string,
  ): T => {
    if (item.id !== id) return item;
    if ("verdict" in steer) return { ...item, verdict: steer.verdict };
    if ("fix" in steer) return { ...item, fix: steer.fix };
    return item;
  };
  if ("claim" in steer) {
    return {
      ...now,
      sections: now.sections.map((section) => ({
        ...section,
        claims: section.claims.map((claim) => mark(claim, steer.claim)),
      })),
    };
  }
  if ("node" in steer) {
    const nodes = now.diagram.nodes.map((node) => mark(node, steer.node));
    return { ...now, diagram: { ...now.diagram, nodes } };
  }
  if ("starter" in steer) {
    return {
      ...now,
      starters: now.starters.map((entry) =>
        entry.id === steer.starter ? { ...entry, selected: steer.selected } : entry,
      ),
    };
  }
  const { priority } = steer;
  if (typeof priority === "string" && "picked" in steer) {
    return {
      ...now,
      priorities: now.priorities.map((entry) =>
        entry.id === priority ? { ...entry, picked: steer.picked } : entry,
      ),
    };
  }
  if (typeof priority === "object") {
    const added = { id: `new:${priority.title}`, title: priority.title, picked: true };
    return { ...now, priorities: [...now.priorities, { ...added, source: "admin" }] };
  }
  return now;
}

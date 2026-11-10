import type { DiagramNode, Understanding } from "../../lib/schema";

/** What decides which boxes the diagram draws: ordering, folding into groups, edges merged
 * onto what is drawn. Pure, so the picture of a platform with a hundred agents can be tested. */

export type Diagram = Understanding["diagram"];
type Edge = Diagram["edges"][number];

/** No column draws more boxes than this, group boxes included. */
export const MOST = 8;

/** Left to right: the person, the agent, what it calls or hands work to, what it keeps. */
export const COLUMN: Record<DiagramNode["kind"], number> = {
  person: 0,
  agent: 1,
  tools: 2,
  worker: 2,
  state: 3,
};

const COLUMNS = 4;

export const OTHER = "Other";

/** Boxes folded into one, by the folder their code sits in. */
export type Group = {
  id: string;
  column: number;
  name: string;
  members: DiagramNode[];
  /** Any member seen in real use. */
  seen: boolean;
  /** The members' asks, summed. */
  asks: number;
  /** The worst mark a member carries: one judged wrong, else one flagged. */
  mark: "wrong" | "flagged" | null;
};

export type Item = { id: string; node: DiagramNode } | { id: string; group: Group };

export type PlanEdge = {
  from: string;
  to: string;
  asks: number;
  seen: boolean;
};

/** A group drawn open: which page of its members shows. */
export type Opened = {
  group: Group;
  page: number;
  /** Members on earlier pages and still to come. */
  before: number;
  after: number;
};

export type Plan = {
  /** Per column, top to bottom; an empty column draws nothing. */
  columns: Item[][];
  edges: PlanEdge[];
  opened: Opened | null;
  /** With a search: how many boxes match. */
  matches: number | null;
};

export type View = { open?: string | null; page?: number; query?: string };

/** The folders of the file a node's evidence names: code first, then a prompt or a doc.
 * `agents/examples/triage/agent.py:12` -> `["agents", "examples", "triage"]`. */
export function foldersOf(node: DiagramNode): string[] {
  const ref =
    node.evidence.find((entry) => entry.kind === "code")?.ref ??
    node.evidence.find((entry) => entry.kind === "prompt" || entry.kind === "docs")?.ref ??
    "";
  const path = (ref.trim().split(/\s+/)[0] ?? "")
    .replace(/^["'`]+|["'`]+$/g, "")
    .replace(/\\/g, "/")
    .replace(/(:\d+)+$/, "")
    .replace(/^\/+/, "");
  if (!path.includes("/") || path.includes("://")) return [];
  return path
    .split("/")
    .slice(0, -1)
    .filter((part) => part && part !== ".");
}

/** How often real use reached each node, and how many edges touch it. */
function weigh(diagram: Diagram) {
  const asks = new Map<string, number>();
  const links = new Map<string, number>();
  for (const edge of diagram.edges) {
    for (const id of [edge.from, edge.to]) {
      asks.set(id, Math.max(asks.get(id) ?? 0, edge.asks));
      links.set(id, (links.get(id) ?? 0) + 1);
    }
  }
  return {
    asks: (id: string) => asks.get(id) ?? 0,
    links: (id: string) => links.get(id) ?? 0,
  };
}

type Weigh = ReturnType<typeof weigh>;

/** Seen in real use first, by asks; then by how many edges touch it; then by title. */
export function order(nodes: readonly DiagramNode[], weight: Weigh): DiagramNode[] {
  return [...nodes].sort(
    (a, b) =>
      Number(b.seen) - Number(a.seen) ||
      weight.asks(b.id) - weight.asks(a.id) ||
      weight.links(b.id) - weight.links(a.id) ||
      a.title.localeCompare(b.title),
  );
}

function makeGroup(column: number, name: string, members: DiagramNode[], weight: Weigh): Group {
  return {
    id: `group:${column}:${name}`,
    column,
    name,
    members,
    seen: members.some((node) => node.seen),
    asks: members.reduce((sum, node) => sum + weight.asks(node.id), 0),
    mark: members.some((node) => node.verdict === "wrong" && !node.fix)
      ? "wrong"
      : members.some((node) => node.flagged)
        ? "flagged"
        : null,
  };
}

/** `nodes` in folder groups, `room` at most: from the deepest folder level that fits, else the
 * top level with the smallest groups merged into "Other". Also how many the top level makes. */
function groupBy(nodes: readonly DiagramNode[], room: number, column: number, weight: Weigh) {
  const folders = new Map(nodes.map((node) => [node.id, foldersOf(node)]));
  const placed = [...folders.values()].filter((parts) => parts.length);
  const deepest = Math.max(0, ...placed.map((parts) => parts.length));
  const split = (depth: number) => {
    const out = new Map<string, DiagramNode[]>();
    for (const node of nodes) {
      const parts = folders.get(node.id) ?? [];
      const name = parts.length ? parts.slice(0, depth).join("/") : OTHER;
      out.set(name, [...(out.get(name) ?? []), node]);
    }
    return out;
  };
  // Folders all the nodes share say nothing: group one level below them.
  let shared = placed.length ? Math.min(...placed.map((parts) => parts.length)) - 1 : 0;
  for (let at = 0; at < shared; at++) {
    if (placed.some((parts) => parts[at] !== placed[0]?.[at])) shared = at;
  }
  const top = split(shared + 1);
  let chosen = top;
  for (let depth = deepest; depth > shared + 1; depth--) {
    const tried = split(depth);
    if (tried.size <= room) {
      chosen = tried;
      break;
    }
  }
  let named = [...chosen]
    .filter(([name]) => name !== OTHER)
    .sort((a, b) => b[1].length - a[1].length);
  let rest = chosen.get(OTHER) ?? [];
  if (named.length + (rest.length ? 1 : 0) > room) {
    // One place is kept for "Other"; the smallest folders go into it.
    const keep = Math.max(0, room - 1);
    rest = [...named.slice(keep).flatMap(([, members]) => members), ...rest];
    named = named.slice(0, keep);
  }
  const groups = named.map(([name, members]) => makeGroup(column, name, members, weight));
  groups.sort(
    (a, b) =>
      Number(b.seen) - Number(a.seen) ||
      b.asks - a.asks ||
      b.members.length - a.members.length ||
      a.name.localeCompare(b.name),
  );
  if (rest.length) groups.push(makeGroup(column, OTHER, order(rest, weight), weight));
  return { groups, fewest: top.size };
}

/** One column's boxes, `room` at most: its seen boxes drawn themselves, most used first, and
 * every never-seen box folded into a group by folder, even with room to spare. Seen boxes give
 * way to groups down to half the room. A column with nothing seen draws its first boxes
 * themselves, filling the room, so a learn before any traffic is not all groups. */
export function fold(
  nodes: readonly DiagramNode[],
  room: number,
  column: number,
  weight: Weigh,
): { shown: DiagramNode[]; groups: Group[] } {
  const seen = nodes.filter((node) => node.seen).length;
  if (seen === nodes.length || !seen) {
    if (nodes.length <= room) return { shown: [...nodes], groups: [] };
  }
  const build = (k: number) => ({
    k,
    ...groupBy(nodes.slice(k), room - k, column, weight),
  });
  let best = build(Math.max(0, Math.min(seen, room - 1)));
  while (best.k > Math.floor(room / 2) && best.fewest > room - best.k) best = build(best.k - 1);
  if (!seen) {
    // Nothing seen: spare room draws the next box itself.
    while (best.k + best.groups.length < room) {
      const next = build(best.k + 1);
      if (next.k + next.groups.length > room) break;
      best = next;
    }
  }
  // A group of one is just its box.
  const lone = best.groups.filter((group) => group.members.length === 1);
  return {
    shown: order([...nodes.slice(0, best.k), ...lone.flatMap((group) => group.members)], weight),
    groups: best.groups.filter((group) => group.members.length > 1),
  };
}

function matches(node: DiagramNode, query: string) {
  const words = [
    node.title,
    node.sub,
    node.fix ?? "",
    ...(node.tools ?? []),
    ...node.evidence.map((e) => e.ref),
  ];
  return words.some((word) => word.toLowerCase().includes(query));
}

/**
 * What to draw of `diagram`: at most `MOST` boxes a column, never-seen ones in folder groups; the
 * group `view.open` drawn open at `view.page`; with `view.query`, only the matching boxes and
 * those that lead to them. Edges go to what is drawn, one per pair, their asks summed.
 */
export function plan(diagram: Diagram, view: View = {}): Plan {
  const weight = weigh(diagram);
  const query = view.query?.trim().toLowerCase() ?? "";
  const hit = query
    ? new Set(diagram.nodes.filter((n) => matches(n, query)).map((n) => n.id))
    : null;
  // Under a search, the boxes that lead to a match stay, so its edge has somewhere to start.
  const leads = new Set<string>();
  if (hit) {
    for (const edge of diagram.edges) if (hit.has(edge.to)) leads.add(edge.from);
  }
  const columns: Item[][] = [];
  const where = new Map<string, string>();
  let opened: Opened | null = null;
  for (let at = 0; at < COLUMNS; at++) {
    const all = order(
      diagram.nodes.filter((node) => COLUMN[node.kind] === at),
      weight,
    );
    const wanted = hit ? all.filter((node) => hit.has(node.id) || leads.has(node.id)) : all;
    const pinned = wanted.filter((node) => hit && !hit.has(node.id));
    const { shown, groups } = fold(
      wanted.filter((node) => !pinned.includes(node)),
      MOST - pinned.length,
      at,
      weight,
    );
    const open = groups.find((group) => group.id === view.open);
    if (open) {
      // Open: the boxes that lead into the group stay, the members page through the rest.
      const into = new Set(
        diagram.edges.filter((e) => open.members.some((m) => m.id === e.to)).map((e) => e.from),
      );
      const hubs = [...pinned, ...shown].filter((node) => into.has(node.id));
      const size = Math.max(1, MOST - hubs.length);
      const pages = Math.max(1, Math.ceil(open.members.length / size));
      const page = Math.min(Math.max(0, view.page ?? 0), pages - 1);
      const members = open.members.slice(page * size, page * size + size);
      opened = {
        group: open,
        page,
        before: page * size,
        after: Math.max(0, open.members.length - (page + 1) * size),
      };
      columns.push([...hubs, ...members].map((node) => ({ id: node.id, node })));
    } else {
      columns.push([
        ...[...pinned, ...shown].map((node) => ({ id: node.id, node })),
        ...groups.map((group) => ({ id: group.id, group })),
      ]);
      for (const group of groups)
        for (const member of group.members) where.set(member.id, group.id);
    }
  }
  for (const column of columns)
    for (const item of column) if ("node" in item) where.set(item.id, item.id);
  return {
    columns,
    edges: merge(diagram.edges, where),
    opened,
    matches: hit ? hit.size : null,
  };
}

/** The edges between what is drawn: each end moved onto its box or its group, one per pair,
 * asks summed, seen when any was; an edge with an end not drawn, or inside one group, goes. */
export function merge(edges: readonly Edge[], where: ReadonlyMap<string, string>): PlanEdge[] {
  const out = new Map<string, PlanEdge>();
  for (const edge of edges) {
    const from = where.get(edge.from);
    const to = where.get(edge.to);
    if (!from || !to || from === to) continue;
    const key = `${from}\u0000${to}`;
    const now = out.get(key);
    const seen = edge.seen && edge.asks > 0;
    if (now) {
      now.asks += edge.asks;
      now.seen ||= seen;
    } else {
      out.set(key, { from, to, asks: edge.asks, seen });
    }
  }
  return [...out.values()];
}

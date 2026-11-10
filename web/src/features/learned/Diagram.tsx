import { ChevronLeft } from "lucide-react";
import type { KeyboardEvent } from "react";
import { useState } from "react";
import { cn } from "../../lib/cn";
import { count } from "../../lib/format";
import type { DiagramNode } from "../../lib/schema";
import { Input } from "../../ui/field";
import { type Diagram, type Group, type Item, plan } from "./diagram-layout";

const WIDTH = [120, 170, 170, 150];

const HEIGHT = 44;

const ROW_GAP = 20;

const COLUMN_GAP = 60;

const PAD = 12;

/** Above this many nodes the diagram gets a search field. */
const SEARCH_FROM = 16;

/** How far an edge between boxes of one column bows out to the right. */
const BOW = 44;

type Box = { item: Item; x: number; y: number; w: number };

/** Places each column's items, the columns centred on the tallest. */
function layout(columns: readonly Item[][]) {
  const used = columns.map((items, at) => ({ items, at })).filter(({ items }) => items.length);
  const span = (n: number) => n * HEIGHT + (n - 1) * ROW_GAP;
  const tallest = Math.max(1, ...used.map(({ items }) => span(items.length)));
  const boxes = new Map<string, Box>();
  let x = PAD;
  for (const { items, at } of used) {
    const w = WIDTH[at] ?? 150;
    const top = PAD + (tallest - span(items.length)) / 2;
    items.forEach((item, row) => {
      boxes.set(item.id, { item, x, y: top + row * (HEIGHT + ROW_GAP), w });
    });
    x += w + COLUMN_GAP;
  }
  // The last column's right edge, plus room for an edge that bows out of it.
  return { boxes, width: x - COLUMN_GAP + PAD + BOW, height: tallest + PAD * 2 };
}

/** A curve from box `a` to box `b`, and where its label sits. */
function curve(a: Box, b: Box) {
  const ay = a.y + HEIGHT / 2;
  const by = b.y + HEIGHT / 2;
  if (a.x === b.x) {
    const x = a.x + a.w;
    return {
      d: `M${x} ${ay} C${x + BOW} ${ay}, ${x + BOW} ${by}, ${x} ${by}`,
      label: { x: x + BOW * 0.75 + 4, y: (ay + by) / 2 + 3 },
    };
  }
  const forward = b.x > a.x;
  const x1 = forward ? a.x + a.w : a.x;
  const x2 = forward ? b.x : b.x + b.w;
  const mid = (x1 + x2) / 2;
  return {
    d: `M${x1} ${ay} C${mid} ${ay}, ${mid} ${by}, ${x2} ${by}`,
    label: { x: mid, y: (ay + by) / 2 - 4 },
  };
}

/** Cuts `text` to about what fits in `width` at a font of `px`. */
function shorten(text: string, width: number, px: number) {
  const chars = Math.floor(width / (px * 0.52));
  return text.length > chars ? `${text.slice(0, Math.max(1, chars - 1))}…` : text;
}

/** Like `shorten`, but keeps the end: a folder's last parts say the most. */
function shortenStart(text: string, width: number, px: number) {
  const chars = Math.floor(width / (px * 0.52));
  return text.length > chars ? `…${text.slice(text.length - Math.max(1, chars - 1))}` : text;
}

/** The small mark in a box's corner: flagged, wrong, fixed or right. */
function markOf(node: DiagramNode) {
  if (node.flagged) return "fill-warning";
  if (node.fix) return "fill-tint";
  if (node.verdict === "wrong") return "fill-fail";
  if (node.verdict === "right") return "fill-pass";
  return null;
}

const LINK =
  "inline-flex items-center gap-1 rounded-md px-1 py-0.5 text-[13px] text-ink-2 hover:text-foreground focus-visible:outline-2 focus-visible:outline-tint";

/**
 * How the target works, drawn from `diagram`: boxes per kind in columns, at most 8 a column,
 * the rest folded by folder into group boxes that open in place; edges as curves thicker the
 * more asks took them, faint and dashed where real use never did. A box opens its check.
 */
export function DiagramView({
  diagram,
  name,
  selected,
  onSelect,
}: {
  diagram: Diagram;
  name: string;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const [query, setQuery] = useState("");
  const searchable = diagram.nodes.length > SEARCH_FROM;
  const shown = plan(diagram, { open, page, query: searchable ? query : "" });
  const { boxes, width, height } = layout(shown.columns);
  const most = Math.max(1, ...shown.edges.map((edge) => edge.asks));
  const openGroup = (id: string) => {
    setOpen(id);
    setPage(0);
  };
  const press = (act: () => void) => (event: KeyboardEvent) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      act();
    }
  };
  const opened = shown.opened;
  return (
    <div className="flex flex-col gap-2 overflow-x-auto px-3">
      {searchable ? (
        <Input
          type="search"
          aria-label="Find in the diagram"
          placeholder="Find a box, tool or file"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setOpen(null);
            setPage(0);
          }}
          className="max-w-[320px]"
        />
      ) : null}
      {opened ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <button type="button" onClick={() => setOpen(null)} className={LINK}>
            <ChevronLeft className="size-3.5" aria-hidden />
            Back to all
          </button>
          <span className="font-mono text-[13px] text-ink-3">
            {opened.group.name} · {opened.group.members.length}
          </span>
        </div>
      ) : null}
      {shown.matches === 0 ? (
        <p className="m-0 text-[13px] text-ink-3">Nothing matches “{query.trim()}”</p>
      ) : (
        <svg
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={`Diagram of how ${name} works`}
          className="block h-auto w-full"
          style={{ minWidth: Math.round(width * 0.78) }}
        >
          <g>
            {shown.edges.map((edge) => {
              const a = boxes.get(edge.from);
              const b = boxes.get(edge.to);
              if (!a || !b) return null;
              const { d, label } = curve(a, b);
              const person = "node" in a.item && a.item.node.kind === "person";
              return (
                <g key={`${edge.from}>${edge.to}`}>
                  <path
                    d={d}
                    fill="none"
                    className={edge.seen ? "stroke-foreground/30" : "stroke-foreground/10"}
                    strokeWidth={edge.seen ? 1.2 + 3.6 * (edge.asks / most) : 1}
                    strokeDasharray={edge.seen ? undefined : "3 4"}
                  />
                  {edge.asks > 0 ? (
                    <text
                      x={label.x}
                      y={label.y}
                      textAnchor="middle"
                      className="fill-ink-3 text-[10px] font-medium"
                    >
                      {person ? count(edge.asks, "ask") : edge.asks}
                    </text>
                  ) : null}
                </g>
              );
            })}
          </g>
          <g>
            {[...boxes.values()].map((box) =>
              "group" in box.item ? (
                <GroupBox
                  key={box.item.id}
                  box={box}
                  group={box.item.group}
                  holdsSelected={box.item.group.members.some((member) => member.id === selected)}
                  onOpen={() => openGroup(box.item.id)}
                  onKeyDown={press(() => openGroup(box.item.id))}
                />
              ) : (
                <NodeBox
                  key={box.item.id}
                  box={box}
                  node={box.item.node}
                  selected={selected === box.item.id}
                  onSelect={() => onSelect(box.item.id)}
                  onKeyDown={press(() => onSelect(box.item.id))}
                />
              ),
            )}
          </g>
        </svg>
      )}
      {opened && (opened.before || opened.after) ? (
        <div className="flex flex-wrap gap-3">
          {opened.before ? (
            <button type="button" onClick={() => setPage(opened.page - 1)} className={LINK}>
              Show earlier
            </button>
          ) : null}
          {opened.after ? (
            <button type="button" onClick={() => setPage(opened.page + 1)} className={LINK}>
              Show {opened.after} more
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function NodeBox({
  box: { x, y, w },
  node,
  selected,
  onSelect,
  onKeyDown,
}: {
  box: Box;
  node: DiagramNode;
  selected: boolean;
  onSelect: () => void;
  onKeyDown: (event: KeyboardEvent) => void;
}) {
  const main = node.kind === "agent";
  const wrong = node.verdict === "wrong" && !node.fix;
  const mark = markOf(node);
  return (
    // biome-ignore lint/a11y/useSemanticElements: an SVG group can't be a <button>.
    <g
      role="button"
      tabIndex={0}
      aria-label={node.title}
      aria-pressed={selected}
      onClick={onSelect}
      onKeyDown={onKeyDown}
      className="group/node outline-none"
    >
      <rect
        x={x}
        y={y}
        width={w}
        height={HEIGHT}
        rx={10}
        strokeDasharray={node.seen ? undefined : "4 3"}
        strokeWidth={selected ? 2.2 : node.verdict ? 1.8 : 1.2}
        className={cn(
          main ? "fill-primary" : node.kind === "state" ? "fill-bubble" : "fill-group",
          selected
            ? "stroke-tint"
            : node.verdict === "right"
              ? "stroke-pass"
              : wrong
                ? "stroke-fail"
                : "stroke-separator group-hover/node:stroke-tint group-focus-visible/node:stroke-tint",
        )}
      />
      {mark ? <circle cx={x + w - 9} cy={y + 9} r={3.5} className={mark} /> : null}
      <text
        x={x + 12}
        y={y + 19}
        className={cn(
          "text-[13px] font-medium",
          main ? "fill-primary-foreground" : "fill-foreground",
          wrong && "line-through",
        )}
      >
        {shorten(node.fix ?? node.title, w - 30, 12.5)}
      </text>
      <text
        x={x + 12}
        y={y + 34}
        className={cn(
          "text-[10.5px]",
          main ? "fill-primary-foreground/70" : "fill-ink-3",
          wrong && "line-through",
        )}
      >
        {shorten(node.seen ? node.sub : node.sub || "never seen in use", w - 24, 10.5)}
      </text>
    </g>
  );
}

function GroupBox({
  box: { x, y, w },
  group,
  holdsSelected,
  onOpen,
  onKeyDown,
}: {
  box: Box;
  group: Group;
  holdsSelected: boolean;
  onOpen: () => void;
  onKeyDown: (event: KeyboardEvent) => void;
}) {
  const size = group.members.length;
  const seen = group.members.filter((member) => member.seen).length;
  const tail = ` · ${size}`;
  const dash = group.seen ? undefined : "4 3";
  return (
    // biome-ignore lint/a11y/useSemanticElements: an SVG group can't be a <button>.
    <g
      role="button"
      tabIndex={0}
      aria-label={`${group.name}, ${count(size, "box", "boxes")}${
        group.mark === "wrong" ? ", one judged wrong" : group.mark ? ", one flagged" : ""
      }`}
      aria-expanded={false}
      onClick={onOpen}
      onKeyDown={onKeyDown}
      className="group/node outline-none"
    >
      {/* A second card behind: a stack of boxes. */}
      <rect
        x={x + 4}
        y={y - 4}
        width={w}
        height={HEIGHT}
        rx={10}
        strokeDasharray={dash}
        strokeWidth={1}
        className="fill-sunk stroke-separator"
      />
      <rect
        x={x}
        y={y}
        width={w}
        height={HEIGHT}
        rx={10}
        strokeDasharray={dash}
        strokeWidth={holdsSelected ? 2.2 : 1.2}
        className={cn(
          "fill-group",
          holdsSelected
            ? "stroke-tint"
            : "stroke-separator group-hover/node:stroke-tint group-focus-visible/node:stroke-tint",
        )}
      />
      {group.mark ? (
        <circle
          cx={x + w - 9}
          cy={y + 9}
          r={3.5}
          className={group.mark === "wrong" ? "fill-fail" : "fill-warning"}
        />
      ) : null}
      <text x={x + 12} y={y + 19} className="fill-foreground text-[13px] font-medium">
        {shortenStart(group.name, w - 30 - tail.length * 6.5, 12.5)}
        {tail}
      </text>
      <text x={x + 12} y={y + 34} className="fill-ink-3 text-[10.5px]">
        {seen ? `${seen} seen in use` : "never seen in use"}
      </text>
    </g>
  );
}

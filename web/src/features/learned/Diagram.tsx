import type { KeyboardEvent } from "react";
import { cn } from "../../lib/cn";
import { count } from "../../lib/format";
import type { DiagramNode, Understanding } from "../../lib/schema";

type Diagram = Understanding["diagram"];

/** Left to right: the person, the agent, what it calls or hands work to, what it keeps. */
const COLUMN: Record<DiagramNode["kind"], number> = {
  person: 0,
  agent: 1,
  tools: 2,
  worker: 2,
  state: 3,
};

const WIDTH = [120, 170, 170, 150];

const HEIGHT = 44;

const ROW_GAP = 20;

const COLUMN_GAP = 60;

const PAD = 12;

/** How far an edge between boxes of one column bows out to the right. */
const BOW = 44;

type Box = { node: DiagramNode; x: number; y: number; w: number };

/** Places each node in its kind's column, the columns centred on the tallest. */
function layout(nodes: readonly DiagramNode[]) {
  const columns = new Map<number, DiagramNode[]>();
  for (const node of nodes) {
    const at = COLUMN[node.kind];
    columns.set(at, [...(columns.get(at) ?? []), node]);
  }
  const used = [...columns.keys()].sort((a, b) => a - b);
  const span = (n: number) => n * HEIGHT + (n - 1) * ROW_GAP;
  const tallest = Math.max(1, ...used.map((at) => span(columns.get(at)?.length ?? 0)));
  const boxes = new Map<string, Box>();
  let x = PAD;
  for (const at of used) {
    const column = columns.get(at) ?? [];
    const w = WIDTH[at] ?? 150;
    const top = PAD + (tallest - span(column.length)) / 2;
    column.forEach((node, row) => {
      boxes.set(node.id, { node, x, y: top + row * (HEIGHT + ROW_GAP), w });
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
function fit(text: string, width: number, px: number) {
  const chars = Math.floor(width / (px * 0.52));
  return text.length > chars ? `${text.slice(0, Math.max(1, chars - 1))}…` : text;
}

/**
 * How the target works, drawn from `diagram`: boxes per kind in columns, edges as curves
 * thicker the more asks took them, dashed where real use never did. A box opens its check.
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
  const { boxes, width, height } = layout(diagram.nodes);
  const most = Math.max(1, ...diagram.edges.map((edge) => edge.asks));
  const keys = (id: string) => (event: KeyboardEvent) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect(id);
    }
  };
  return (
    <div className="overflow-x-auto px-3">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`Diagram of how ${name} works`}
        className="block h-auto w-full"
        style={{ minWidth: Math.round(width * 0.78) }}
      >
        <g>
          {diagram.edges.map((edge, index) => {
            const a = boxes.get(edge.from);
            const b = boxes.get(edge.to);
            if (!a || !b) return null;
            const { d, label } = curve(a, b);
            const seen = edge.seen && edge.asks > 0;
            return (
              <g key={index}>
                <path
                  d={d}
                  fill="none"
                  className={seen ? "stroke-foreground/30" : "stroke-foreground/20"}
                  strokeWidth={seen ? 1.2 + 3.6 * (edge.asks / most) : 1.2}
                  strokeDasharray={seen ? undefined : "4 4"}
                />
                {edge.asks > 0 ? (
                  <text
                    x={label.x}
                    y={label.y}
                    textAnchor="middle"
                    className="fill-ink-3 text-[10px] font-medium"
                  >
                    {a.node.kind === "person" ? count(edge.asks, "ask") : edge.asks}
                  </text>
                ) : null}
              </g>
            );
          })}
        </g>
        <g>
          {[...boxes.values()].map(({ node, x, y, w }) => {
            const main = node.kind === "agent";
            const wrong = node.verdict === "wrong";
            return (
              // biome-ignore lint/a11y/useSemanticElements: an SVG group can't be a <button>.
              <g
                key={node.id}
                role="button"
                tabIndex={0}
                aria-label={node.title}
                aria-pressed={selected === node.id}
                onClick={() => onSelect(node.id)}
                onKeyDown={keys(node.id)}
                className="group/node outline-none"
              >
                <rect
                  x={x}
                  y={y}
                  width={w}
                  height={HEIGHT}
                  rx={10}
                  strokeDasharray={node.seen ? undefined : "4 3"}
                  strokeWidth={selected === node.id ? 2.2 : node.verdict ? 1.8 : 1.2}
                  className={cn(
                    main ? "fill-primary" : node.kind === "state" ? "fill-bubble" : "fill-group",
                    selected === node.id
                      ? "stroke-tint"
                      : node.verdict === "right"
                        ? "stroke-success"
                        : wrong
                          ? "stroke-destructive"
                          : "stroke-separator group-hover/node:stroke-tint group-focus-visible/node:stroke-tint",
                  )}
                />
                <text
                  x={x + 12}
                  y={y + 19}
                  className={cn(
                    "text-[12.5px] font-medium",
                    main ? "fill-primary-foreground" : "fill-foreground",
                    wrong && "line-through",
                  )}
                >
                  {fit(node.title, w - 24, 12.5)}
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
                  {fit(node.sub, w - 24, 10.5)}
                </text>
              </g>
            );
          })}
        </g>
      </svg>
    </div>
  );
}

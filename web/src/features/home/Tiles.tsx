import { cn } from "../../lib/cn";
import { count, usd } from "../../lib/format";
import type { Health, Period } from "../../lib/schema";
import { verdictWord } from "../../lib/verdict";
import { NOW } from "./period";

type Tone = "good" | "bad" | null;

export function Tiles({ health, period }: { health: Health; period: Period }) {
  const { passing, consistent, reply_seconds: reply, cost } = health.tiles;
  const tiles: { label: string; value: string; note?: string; tone: Tone }[] = [];
  if (passing?.total) {
    const delta = passing.delta;
    tiles.push({
      label: "Tests passing",
      value: `${passing.value} of ${passing.total}`,
      note:
        [
          delta === null
            ? null
            : delta === 0
              ? "Same as the last run"
              : `${Math.abs(delta)} ${delta > 0 ? "more" : "fewer"} than the last run`,
          passing.model_only ? `${passing.model_only} judged by a model only` : null,
          passing.unjudged ? `${passing.unjudged} ${verdictWord("unjudged").toLowerCase()}` : null,
        ]
          .filter(Boolean)
          .join(" · ") || undefined,
      tone: delta ? (delta > 0 ? "good" : "bad") : null,
    });
  }
  if (consistent?.total) {
    const swinging = consistent.total - consistent.value;
    tiles.push({
      label: "Consistent answers",
      value: `${consistent.value} of ${consistent.total}`,
      note: swinging
        ? `${count(swinging, "test")} ${swinging === 1 ? "changes its" : "change their"} mind`
        : undefined,
      tone: swinging ? "bad" : null,
    });
  }
  if (reply && reply.value !== null) {
    const delta = reply.delta === null ? 0 : Math.round(reply.delta);
    tiles.push({
      label: "Typical reply",
      value: `${Math.round(reply.value)} s`,
      note: delta ? `${Math.abs(delta)} s ${delta < 0 ? "faster" : "slower"}` : undefined,
      tone: delta ? (delta < 0 ? "good" : "bad") : null,
    });
  }
  // Only tries with a recorded cost: the per-question field has always meant per try.
  if (cost?.per_question) {
    tiles.push({
      label: "Spent per try",
      value: usd(cost.per_question),
      note: `${usd(cost.period_total)} spent${NOW[period] ? ` ${NOW[period]}` : ""}`,
      tone: null,
    });
  }
  if (!tiles.length) return null;
  return (
    <section className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,200px),1fr))] gap-3">
      {tiles.map((tile) => (
        <div key={tile.label} className="flex flex-col gap-1 glass rounded-2xl p-4">
          <span className="text-[13px] text-ink-2">{tile.label}</span>
          <span className="font-display text-[30px] leading-tight font-semibold tracking-[-0.03em] tabular-nums">
            {tile.value}
          </span>
          {tile.note ? (
            <span
              className={cn(
                "text-[12.5px] font-semibold",
                tile.tone === "good"
                  ? "text-tint"
                  : tile.tone === "bad"
                    ? "text-warning"
                    : "text-ink-2",
              )}
            >
              {tile.note}
            </span>
          ) : null}
        </div>
      ))}
    </section>
  );
}

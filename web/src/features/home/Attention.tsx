import { cn } from "../../lib/cn";
import type { Health } from "../../lib/schema";
import { CARD } from "./card";

const ACTION: Record<Health["attention"][number]["action"], string> = {
  see_why: "See why",
  open_test: "Open test",
  review: "Review",
};

/** What to look at, most pressing first. */
export function Attention({
  items,
  onAct,
}: {
  items: Health["attention"];
  onAct: (item: Health["attention"][number]) => void;
}) {
  return (
    <div className={cn(CARD, "flex flex-col gap-1")}>
      <div className="mb-2 flex items-center gap-2">
        <h2 className="m-0 text-[17px] font-bold">Needs your attention</h2>
        <span className="rounded-full bg-warning/12 px-2 py-0.5 text-[12.5px] font-bold text-warning">
          {items.length}
        </span>
      </div>
      {items.map((item, index) => (
        <button
          key={index}
          type="button"
          onClick={() => onAct(item)}
          className="-mx-3 flex items-start gap-3 rounded-[10px] p-3 text-start hover:bg-selection focus-visible:outline-2 focus-visible:outline-ring"
        >
          <span
            className={cn(
              "mt-[5px] size-2.5 shrink-0 rounded-full",
              item.action === "review" ? "bg-mid" : "bg-warning",
            )}
          />
          <span className="flex min-w-0 flex-1 flex-col gap-0.5">
            <span className="text-[14px] font-semibold">{item.title}</span>
            {item.detail ? (
              <span className="text-[14px] leading-[1.45] text-ink-2">{item.detail}</span>
            ) : null}
          </span>
          <span className="text-[14px] font-semibold whitespace-nowrap text-tint">
            {ACTION[item.action]}
          </span>
        </button>
      ))}
    </div>
  );
}

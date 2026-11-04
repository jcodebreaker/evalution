import { ChartColumn, Gauge, House, ListChecks, Network, Plus, Settings } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import { progressText } from "../../lib/format";
import type { Place, View } from "../../lib/router";
import type { RunSummary, SetSummary, Target } from "../../lib/schema";
import { Spinner } from "../../ui/loading";
import { Picker } from "../../ui/menu";
import type { Card } from "../learned/ProjectCard";
import { CardRow } from "../learned/ProjectCard";

/**
 * The app picker (with Connect), then Home, What it learned, Tests, Benchmarks and Runs (with the going run
 * under it); Settings at the bottom.
 */
export function Sidebar({
  place,
  targets,
  target,
  sets,
  set,
  going,
  goingLabel,
  cards,
  onTarget,
  onSet,
  onView,
  onGoing,
}: {
  place: Place;
  targets: Target[];
  target: string | null;
  sets: SetSummary[];
  set: string | null;
  going: RunSummary | null;
  goingLabel: string;
  /** Each app's card, once read: its row in the picker. */
  cards: ReadonlyMap<string, Card | null> | null;
  onTarget: (name: string) => void;
  onSet: (set: string) => void;
  onView: (view: View) => void;
  onGoing: () => void;
}) {
  const setName = sets.find((entry) => entry.set === set)?.name ?? set ?? "";
  const item = (view: View, icon: ReactNode, label: string) => (
    <NavItem
      selected={place.view === view && !(view === "runs" && going && place.run === going.run)}
      onClick={() => onView(view)}
      icon={icon}
      label={label}
    />
  );
  const card = target ? (cards?.get(target) ?? null) : null;
  return (
    <aside aria-label="Navigation" className="glass flex min-h-0 flex-col">
      <div className="flex items-center gap-2.5 px-5 pt-5 pb-3 max-md:hidden">
        <Logo />
        <span className="font-display text-[17px] font-semibold tracking-[-0.02em]">evalution</span>
      </div>
      {target ? (
        <div className="flex min-w-0 flex-col gap-1 px-3 pt-1 pb-2 max-md:pt-3">
          <Picker
            label="App"
            value={
              <span className="flex min-w-0 items-center gap-2.5">
                <span className="grid size-[26px] shrink-0 place-items-center rounded-[7px] bg-selection-strong text-[12px] font-bold text-tint">
                  {target.slice(0, 1).toUpperCase()}
                </span>
                <span className="flex min-w-0 flex-col text-start">
                  <span className="truncate font-semibold">{target}</span>
                  {card?.one_line ? (
                    <span className="truncate text-[12px] font-normal text-ink-2">
                      {card.one_line}
                    </span>
                  ) : null}
                </span>
              </span>
            }
            options={targets.map((entry) => ({
              value: entry.name,
              label: <CardRow name={entry.name} card={cards?.get(entry.name) ?? null} />,
            }))}
            onPick={onTarget}
            actions={[
              {
                label: (
                  <>
                    <Plus className="size-3.5" aria-hidden />
                    Connect an app
                  </>
                ),
                onClick: () => onView("connect"),
              },
            ]}
            className="w-full justify-between rounded-[14px] px-2.5 py-2 font-normal ring-1 ring-separator"
          />
          {sets.length > 1 ? (
            <Picker
              label="Test set"
              value={setName}
              options={sets.map((entry) => ({ value: entry.set, label: entry.name }))}
              onPick={onSet}
              className="self-start text-[12px] font-medium text-ink-2"
            />
          ) : null}
        </div>
      ) : null}
      <nav
        className={cn(
          "flex min-h-0 flex-1 flex-col gap-0.5 overflow-auto px-3 pt-2 pb-2.5 max-md:flex-row max-md:flex-wrap",
          !target && "hidden",
        )}
      >
        {item("home", <House className="size-4" aria-hidden />, "Home")}
        {item("app", <Network className="size-4" aria-hidden />, "What it learned")}
        {item("tests", <ListChecks className="size-4" aria-hidden />, "Tests")}
        {item("benchmarks", <Gauge className="size-4" aria-hidden />, "Benchmarks")}
        {item("runs", <ChartColumn className="size-4" aria-hidden />, "Runs")}
        {going ? (
          <button
            type="button"
            onClick={onGoing}
            aria-current={place.run === going.run ? "page" : undefined}
            className={cn(
              "flex w-full min-w-0 items-center gap-2 rounded-lg py-1.5 ps-9 pe-2 text-start text-[12.5px] focus-visible:outline-2 focus-visible:outline-ring max-md:w-auto max-md:ps-2",
              place.run === going.run ? "bg-selection-strong text-tint" : "hover:bg-selection",
            )}
          >
            <Spinner />
            <span className="truncate">
              {goingLabel} · {going.analyzing ? "Analyzing" : progressText(going)}
            </span>
          </button>
        ) : null}
      </nav>
      <div className="px-3 pb-3 max-md:pt-0">
        {item("model", <Settings className="size-4" aria-hidden />, "Settings")}
      </div>
    </aside>
  );
}

function NavItem({
  icon,
  label,
  selected,
  onClick,
}: {
  icon: ReactNode;
  label: string;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-current={selected ? "page" : undefined}
      onClick={onClick}
      className={cn(
        "flex w-full min-w-0 items-center gap-2.5 rounded-lg px-2.5 py-2 text-start text-[13.5px] focus-visible:outline-2 focus-visible:outline-ring max-md:w-auto",
        selected ? "bg-selection-strong font-semibold text-tint" : "text-ink hover:bg-selection",
      )}
    >
      <span className={cn("grid size-4 shrink-0 place-items-center", !selected && "text-ink-2")}>
        {icon}
      </span>
      <span className="min-w-0 flex-1 truncate">{label}</span>
    </button>
  );
}

/**
 * The mark: an `e` in one even stroke. A line across the centre of a circle of radius 22, then 320 degrees
 * around it, leaving 40 open under the line.
 */
function Logo() {
  return (
    <svg viewBox="0 0 64 64" aria-hidden className="size-7 shrink-0 text-tint">
      <path
        d="M10 32H54A22 22 0 1 0 48.853 46.141"
        fill="none"
        stroke="currentColor"
        strokeWidth="10"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

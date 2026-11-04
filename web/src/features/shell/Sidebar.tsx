import {
  ChartColumn,
  Gauge,
  House,
  ListChecks,
  Network,
  Plus,
  Settings,
  Sparkles,
} from "lucide-react";
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
  asking,
  onAskAi,
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
  asking: boolean;
  onAskAi: (() => void) | null;
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
      <div className="flex items-center gap-2.5 border-b border-separator px-4 py-3.5 max-md:hidden">
        <Logo />
        <span className="text-[16px] font-semibold tracking-[-0.02em]">evalution</span>
      </div>
      {target ? (
        <div className="flex min-w-0 flex-col gap-1 px-3 pt-3 pb-2 max-md:pt-3">
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
            className="w-full justify-between rounded-lg bg-muted px-2.5 py-2 font-normal"
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
        <NavLabel>Your tests</NavLabel>
        {item("tests", <ListChecks className="size-4" aria-hidden />, "Tests")}
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
              {goingLabel} ·{" "}
              {going.analyzing
                ? "Analyzing"
                : progressText(going, goingLabel === "Benchmark" ? "questions" : "tests")}
            </span>
          </button>
        ) : null}
        <NavLabel>Standard</NavLabel>
        {item("benchmarks", <Gauge className="size-4" aria-hidden />, "Benchmarks")}
      </nav>
      <div className="flex flex-col gap-0.5 px-3 pb-3 max-md:pt-0">
        {onAskAi ? (
          <NavItem
            selected={false}
            onClick={onAskAi}
            icon={asking ? <Spinner /> : <Sparkles className="size-4 text-tint" aria-hidden />}
            label="Ask AI"
          />
        ) : null}
        {item("model", <Settings className="size-4" aria-hidden />, "Settings")}
      </div>
    </aside>
  );
}

function NavLabel({ children }: { children: ReactNode }) {
  return (
    <span className="px-2.5 pt-3 pb-1 text-[10.5px] font-medium tracking-[0.06em] text-ink-3 uppercase max-md:hidden">
      {children}
    </span>
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
        "flex w-full min-w-0 items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-start text-[13px] focus-visible:outline-2 focus-visible:outline-ring max-md:w-auto",
        selected ? "bg-muted font-medium text-ink" : "text-ink-2 hover:bg-selection",
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
/** The mark: a spider chart whose data shape is the bot. */
function Logo() {
  return (
    <svg viewBox="0 0 64 64" aria-hidden className="size-7 shrink-0">
      <path
        d="M57.9 21.3 42.7 6.1H21.3L6.1 21.3v21.4l15.2 15.2h21.4l15.2-15.2z"
        fill="none"
        strokeWidth="1.5"
        strokeLinejoin="round"
        className="stroke-foreground opacity-30"
      />
      <path
        d="M6.1 21.3 57.9 42.7M57.9 21.3 6.1 42.7M21.3 6.1l21.4 51.8M42.7 6.1 21.3 57.9"
        fill="none"
        className="stroke-foreground opacity-20"
      />
      <path
        d="M54.2 22.8 37.7 18.1H26.3L9.8 22.8l3.7 16.9 13.1 5.2h10.8l13.1-5.2z"
        strokeWidth="2.5"
        strokeLinejoin="round"
        className="fill-tint stroke-tint"
      />
      <circle cx="26.5" cy="29.5" r="3.2" className="fill-primary-foreground" />
      <circle cx="37.5" cy="29.5" r="3.2" className="fill-primary-foreground" />
    </svg>
  );
}

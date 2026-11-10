import {
  ChartColumn,
  Gauge,
  House,
  ListChecks,
  Lock,
  Monitor,
  Network,
  Plus,
  Settings,
} from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import type { Place, View } from "../../lib/router";
import type { Isolation, SetSummary, Target } from "../../lib/schema";
import { Picker } from "../../ui/menu";
import { useAccount } from "../account/account";
import { WorkspaceMenu } from "../account/WorkspaceMenu";
import type { Card } from "../learned/ProjectCard";
import { CardRow } from "../learned/ProjectCard";
import { Logo } from "./Logo";

/**
 * The mark, the app picker (with Connect) and its test set; then Overview, Tests, Runs and
 * Benchmarks; under the app's name, What it learned and Test environment; at the foot, that tests run
 * on a copy (once its plan is proven) and Settings. The assistant has its own button, not an item.
 */
export function Sidebar({
  place,
  targets,
  target,
  sets,
  set,
  tests = null,
  failing = 0,
  plan = null,
  cards,
  onTarget,
  onSet,
  onView,
}: {
  place: Place;
  targets: Target[];
  target: string | null;
  sets: SetSummary[];
  set: string | null;
  /** How many tests the set has, once read. */
  tests?: number | null;
  /** How many tests failed in the last run that finished. */
  failing?: number;
  /** The app's isolation plan, once read: shown at the foot only when proven. */
  plan?: Pick<Isolation, "status" | "version"> | null;
  /** Each app's card, once read: its row in the picker. */
  cards: ReadonlyMap<string, Card | null> | null;
  onTarget: (name: string) => void;
  onSet: (set: string) => void;
  onView: (view: View) => void;
}) {
  const setName = sets.find((entry) => entry.set === set)?.name ?? set ?? "";
  const item = (view: View, icon: ReactNode, label: string, count?: string, bad?: boolean) => (
    <NavItem
      selected={place.view === view}
      onClick={() => onView(view)}
      icon={icon}
      label={label}
      count={count}
      bad={bad}
    />
  );
  const card = target ? (cards?.get(target) ?? null) : null;
  const account = useAccount();
  return (
    <aside
      aria-label="Navigation"
      className="flex min-h-0 flex-col border-e border-separator bg-group p-3 max-md:border-e-0 max-md:border-b"
    >
      <div
        className={cn(
          "flex items-center gap-2 px-2 pt-1 pb-3",
          account ? "min-w-0" : "max-md:hidden",
        )}
      >
        <Logo />
        {account ? (
          <WorkspaceMenu account={account} />
        ) : (
          <span className="text-[16px] leading-none font-semibold tracking-[-0.03em]">
            evalution
          </span>
        )}
      </div>
      {target ? (
        <div className="flex min-w-0 flex-col">
          <Picker
            label="App"
            value={
              <span className="flex min-w-0 items-center gap-3">
                <span className="grid size-[30px] shrink-0 place-items-center rounded-[7px] bg-tint-soft font-semibold text-tint-ink">
                  {target.slice(0, 1).toUpperCase()}
                </span>
                <span className="flex min-w-0 flex-col text-start leading-[1.25]">
                  <span className="truncate font-semibold">{target}</span>
                  {card?.one_line ? (
                    <span className="truncate text-[12px] font-normal text-ink-3">
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
            className="w-full justify-between rounded-[10px] border border-separator bg-group p-2 font-normal [&>svg]:size-4 [&>svg]:text-ink-3 [&>svg]:opacity-100"
          />
          {setName ? (
            <div className="mx-2 mt-2 flex min-w-0 items-center gap-1.5 text-[13px] text-ink-2">
              <span className="shrink-0">Test set</span>
              {sets.length > 1 ? (
                <Picker
                  label="Test set"
                  value={setName}
                  options={sets.map((entry) => ({ value: entry.set, label: entry.name }))}
                  onPick={onSet}
                  className="-ms-1 px-1 py-0 text-[13px] font-medium text-ink"
                />
              ) : (
                <b className="truncate font-medium text-ink">{setName}</b>
              )}
            </div>
          ) : null}
        </div>
      ) : null}
      <nav
        aria-label="Main"
        className={cn(
          "mt-4 flex min-h-0 flex-1 flex-col gap-px overflow-auto max-md:mt-2 max-md:flex-row max-md:flex-wrap",
          !target && "hidden",
        )}
      >
        {item("home", <House aria-hidden />, "Overview")}
        {item("tests", <ListChecks aria-hidden />, "Tests", tests ? String(tests) : undefined)}
        {item(
          "runs",
          <ChartColumn aria-hidden />,
          "Runs",
          failing ? `${failing} failing` : undefined,
          true,
        )}
        {item("benchmarks", <Gauge aria-hidden />, "Benchmarks")}
        {target ? (
          <span className="truncate px-2 pt-4 pb-1 text-[12px] text-ink-3 max-md:hidden">
            {target}
          </span>
        ) : null}
        {item("app", <Network aria-hidden />, "What it learned")}
        {item("environment", <Monitor aria-hidden />, "Test environment")}
      </nav>
      <div className="flex flex-col gap-2 pt-2">
        {target && plan?.status === "proven" ? (
          <button
            type="button"
            onClick={() => onView("environment")}
            title={`${
              plan.version === null ? "Isolation plan proven" : `Plan v${plan.version} proven`
            }: open the Test environment`}
            className="flex h-[34px] items-center gap-2 rounded-[7px] bg-pass-soft px-2 text-start text-[13px] whitespace-nowrap text-pass focus-visible:outline-2 focus-visible:outline-tint max-md:hidden"
          >
            <Lock className="size-[15px] shrink-0" aria-hidden />
            Tests run on a copy
          </button>
        ) : null}
        <div className="flex flex-col gap-px max-md:flex-row">
          {item("model", <Settings aria-hidden />, "Settings")}
        </div>
      </div>
    </aside>
  );
}

/** A nav item's number: quiet, or red when it counts failures. */
function Count({ bad = false, children }: { bad?: boolean; children: ReactNode }) {
  return (
    <span
      className={cn(
        "ms-auto shrink-0 rounded-full px-1.5 font-mono text-[11px] leading-[18px] font-medium tabular-nums",
        bad
          ? "bg-fail-soft text-fail"
          : "bg-sunk text-ink-3 group-aria-[current=page]:bg-group group-aria-[current=page]:text-tint-ink",
      )}
    >
      {children}
    </span>
  );
}

function NavItem({
  icon,
  label,
  count,
  bad = false,
  selected,
  onClick,
}: {
  icon: ReactNode;
  label: string;
  /** A number beside it, said with its name: "Runs, 3 failing". */
  count?: string;
  bad?: boolean;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-current={selected ? "page" : undefined}
      aria-label={count ? `${label}, ${count}` : undefined}
      onClick={onClick}
      className={cn(
        "group flex h-[34px] w-full min-w-0 items-center gap-3 rounded-[7px] px-2 text-start text-[14px] focus-visible:outline-2 focus-visible:outline-tint max-md:w-auto",
        selected
          ? "bg-tint-soft font-medium text-tint-ink"
          : "text-ink-2 hover:bg-sunk hover:text-ink",
      )}
    >
      <span className="grid size-4 shrink-0 place-items-center [&_svg]:size-4">{icon}</span>
      <span className="min-w-0 truncate">{label}</span>
      {count ? <Count bad={bad}>{count}</Count> : null}
    </button>
  );
}

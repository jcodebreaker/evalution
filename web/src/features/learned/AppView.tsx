import { RotateCw, ThumbsDown, ThumbsUp, TriangleAlert } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { api, appApi } from "../../lib/api";
import { copy } from "../../lib/clipboard";
import { cn } from "../../lib/cn";
import { ago, seconds as secondsText } from "../../lib/format";
import type { AgentSession, Claim, Steer, UnderstandingView } from "../../lib/schema";
import { POLL_MS, ready, useLoad, useVisiblePoll } from "../../lib/use-load";
import { Button } from "../../ui/button";
import { Skeleton } from "../../ui/field";
import { MoreMenu } from "../../ui/menu";
import { Connection } from "../connect/Connection";
import { Card, CardHead, Chip, Screen, ScreenHead } from "../connect/kit";
import { Fold } from "../shared/group";
import { OutOfDate } from "../shared/KnowledgeUpdate";
import { FirstPass, isFirstPass, LearnedAreas, LeftOut, learnState } from "./Areas";
import { ClaimText } from "./ClaimText";
import { DiagramView } from "./Diagram";
import { LastChanges } from "./LastChanges";
import { LastLearn } from "./LastLearn";
import type { LearnAreaFn } from "./LearnArea";
import { Learning } from "./Learning";
import { NodeCheck } from "./NodeCheck";
import { Paths } from "./Paths";
import { Priorities } from "./Priorities";
import { Suggested } from "./Suggested";
import { apply } from "./steer";
import { Tools } from "./Tools";

/** The sections a person reads: the overview's. An area's section and the Details are the
 * test writer's notes, kept for it, not shown unless the page lists the areas. */
const forPeople = (section: { id: string }) =>
  !section.id.startsWith("area-") && section.id !== "details" && section.id !== "what_it_is";

/** The sections the areas wrote, in the areas' order (heaviest first). */
function areaSections(understanding: UnderstandingView): UnderstandingView["sections"] {
  const written = understanding.sections.filter((section) => section.id.startsWith("area-"));
  const ordered = (understanding.progress?.areas ?? []).flatMap((area) => {
    const found = written.find(
      (section) =>
        section.id === `area-${area.id}` || (area.title !== "" && section.title === area.title),
    );
    return found ? [found] : [];
  });
  return [...ordered, ...written.filter((section) => !ordered.includes(section))];
}

/** Claims a section shows before "Show more". */
const SHOWN = 5;

/** A section's first claims, and any flagged one (it wants the owner); all of them when opened. */
function shownClaims<T extends { flagged?: boolean }>(claims: T[], all: boolean): T[] {
  return all ? claims : claims.filter((claim, index) => index < SHOWN || claim.flagged);
}

type Tab = "tools" | "paths" | "knows" | "areas" | "suggested";

function Figure({ value, label, sub }: { value: string; label: string; sub?: string }) {
  return (
    <Card>
      <div className="p-5 max-md:p-4">
        <div className="text-[32px] leading-none font-semibold tracking-[-0.035em] tabular-nums">
          {value}
        </div>
        <p className="m-0 mt-1.5">{label}</p>
        {sub ? <p className="m-0 truncate text-sm text-ink-3">{sub}</p> : null}
      </div>
    </Card>
  );
}

/** One claim to check: its words and where they come from, then right or wrong. */
function ClaimRow({ claim, onSteer }: { claim: Claim; onSteer: (steer: Steer) => void }) {
  return (
    <li
      data-flagged={claim.flagged ? "" : undefined}
      className="flex items-start gap-3.5 border-rule px-5 py-3 not-last:border-b max-md:px-4"
    >
      <div className="min-w-0 flex-1">
        <ClaimText text={claim.text} item={claim} />
      </div>
      <span className="inline-flex shrink-0 gap-0.5">
        {(
          [
            ["right", ThumbsUp, "Right"],
            ["wrong", ThumbsDown, "Wrong"],
          ] as const
        ).map(([verdict, Icon, label]) => (
          <button
            key={verdict}
            type="button"
            aria-label={label}
            aria-pressed={claim.verdict === verdict}
            onClick={() => onSteer({ claim: claim.id, verdict })}
            className={cn(
              "grid size-7 place-items-center rounded-[7px] text-ink-3 hover:bg-sunk hover:text-ink focus-visible:outline-2 focus-visible:outline-tint",
              claim.verdict === verdict &&
                (verdict === "right" ? "bg-pass-soft text-pass" : "bg-fail-soft text-fail"),
            )}
          >
            <Icon aria-hidden className="size-[15px]" />
          </button>
        ))}
      </span>
    </li>
  );
}

/**
 * What it learned: what the app does in a line, its stack and whether it changed, four figures,
 * how it works with the box picked checked beside it, then Tools, Typical paths, What it knows,
 * Areas and Suggested tests. Every check is sent as a steer and shows at once.
 */
export function AppView({
  target,
  agent,
  onLearn,
  onLearnArea,
  onAdd,
  onSaved,
  onRemoved,
}: {
  target: string;
  /** The test writer's thread, while it may be learning this app. */
  agent: AgentSession | null;
  onLearn: () => void;
  /** "Learn this area", for an area a learn didn't learn. */
  onLearnArea?: LearnAreaFn;
  onAdd: (starters: string[]) => Promise<void>;
  onSaved: () => Promise<unknown>;
  onRemoved: () => void;
}) {
  const [load, reload] = useLoad(`understanding:${target}`, () => appApi.understanding(target));
  const [shown, setShown] = useState<UnderstandingView | null>(null);
  const [node, setNode] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [all, setAll] = useState<Set<string>>(() => new Set());
  const [tab, setTab] = useState<Tab | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const loaded = ready(load);
  useEffect(() => setShown(loaded), [loaded]);

  // While the writer works, what it learns shows up here.
  const working = agent?.status === "working" && agent.target === target;
  const messages = agent?.messages.length ?? 0;
  // A learn the understanding says is running: its areas land as it goes.
  const live = loaded?.progress?.state === "learning";
  useVisiblePoll(reload, POLL_MS, working || live);
  useEffect(() => {
    if (messages) void reload();
  }, [messages, reload]);

  // Opening the page is seeing what changed; a claim that wants the owner comes into view.
  const unseen = loaded?.knowledge.last ?? null;
  useEffect(() => {
    if (!unseen) return;
    void api.sawKnowledge(target).catch(() => undefined);
    if (unseen.flagged) {
      setTab("knows");
      window.setTimeout(
        () => document.querySelector("[data-flagged]")?.scrollIntoView({ block: "center" }),
        0,
      );
    }
  }, [unseen, target]);

  const undo = () => {
    setError(null);
    appApi.undoKnowledge(target).then(setShown, () => setError("Couldn't undo that. Try again."));
  };

  const steer = (next: Steer) => {
    if (!shown) return;
    const before = shown;
    setShown(apply(shown, next));
    setError(null);
    appApi.steer(target, next).then(
      (answer) => {
        if (answer) setShown(answer);
      },
      () => {
        setShown(before);
        setError("Couldn't save that. Try again.");
      },
    );
  };

  const copyCard = async () => {
    let done = false;
    try {
      done = await copy(await api.cardMarkdown(target));
    } catch {
      done = false;
    }
    setCopied(done ? "Copied" : "Couldn't copy");
    window.setTimeout(() => setCopied(null), 1800);
  };

  const learnButton = (
    <Button variant="secondary" size="lg" disabled={working} onClick={onLearn}>
      <RotateCw />
      {shown?.version ? "Learn again" : "Learn"}
    </Button>
  );
  const connection = (
    <Fold title="Connection">
      <Connection target={target} onSaved={onSaved} onRemoved={onRemoved} />
    </Fold>
  );
  if (load.status === "loading" || (load.status === "ready" && !shown)) {
    return (
      <Screen>
        <ScreenHead title="What it learned" actions={learnButton} />
        <Skeleton className="h-28 rounded-2xl" />
        <Skeleton className="h-80 rounded-2xl" />
      </Screen>
    );
  }
  if (load.status !== "ready" || !shown) {
    return (
      <Screen>
        <ScreenHead title="What it learned" actions={learnButton} />
        <p className="m-0 text-ink-2">Couldn't load what it learned.</p>
        {connection}
      </Screen>
    );
  }

  const { card } = shown;
  const { numbers } = card;
  const progress = learnState(shown);
  const areasShown = Boolean(progress?.areas.length);
  const session = agent?.target === target ? agent : null;
  const activity = working ? (
    <Learning session={agent} onLearnArea={onLearnArea} areasShown={areasShown} />
  ) : (
    <LastLearn session={session} onLearnArea={onLearnArea} areasShown={areasShown} />
  );
  const status =
    card.status === "learning" ? (
      <Chip tone="tint">{working ? "Learning" : "Not learned"}</Chip>
    ) : card.status === "out_of_date" ? (
      <Chip tone="warn">
        {card.changes ? `Changed since learned · ${card.changes}` : "Changed since learned"}
      </Chip>
    ) : null;
  const tests =
    numbers.tests_passing === null
      ? numbers.tests
        ? "not run yet"
        : undefined
      : `${numbers.tests_passing} pass · ${numbers.tests - numbers.tests_passing} fail`;

  const nodes = shown.diagram.nodes;
  const picked =
    nodes.find((entry) => entry.id === node) ??
    nodes.find((entry) => entry.kind === "agent") ??
    nodes[0] ??
    null;

  const sections = [...shown.sections.filter(forPeople), ...(progress ? areaSections(shown) : [])];
  const claims = sections.reduce((sum, section) => sum + section.claims.length, 0);
  const areaCount = progress?.areas.length ?? 0;
  const tabs: { id: Tab; label: string; n?: number }[] = [
    { id: "tools", label: "Tools", n: numbers.tools || undefined },
    { id: "paths", label: "Typical paths", n: shown.paths.length || undefined },
    { id: "knows", label: "What it knows", n: claims || undefined },
    { id: "areas", label: "Areas", n: areaCount || undefined },
    { id: "suggested", label: "Suggested tests", n: shown.starters.length || undefined },
  ];
  const current: Tab = tab ?? (live && areaCount ? "areas" : "tools");
  const empty = (words: string) => <p className="m-0 px-1 text-ink-3">{words}</p>;

  let panel: ReactNode;
  switch (current) {
    case "tools":
      panel = <Tools target={target} />;
      break;
    case "paths":
      panel = shown.paths.length ? (
        <Card>
          <Paths paths={shown.paths} />
        </Card>
      ) : (
        empty("No paths seen yet")
      );
      break;
    case "knows":
      panel = (
        <div className="flex flex-col gap-5">
          {card.summary ? (
            <Card aria-label="What it is">
              <CardHead title="What it is" />
              <p className="m-0 max-w-[72ch] p-5 leading-relaxed max-md:p-4">{card.summary}</p>
            </Card>
          ) : null}
          {sections.map((section) => (
            <Card key={section.id} aria-label={section.title}>
              <CardHead title={section.title}>
                {section.claims.some((claim) => claim.flagged || claim.stale === "direct") ? (
                  <Chip tone="warn">May be out of date</Chip>
                ) : null}
              </CardHead>
              {section.note ? (
                <p className="m-0 mx-5 mt-3 flex items-start gap-2.5 rounded-[10px] bg-unjudged-soft px-3 py-2.5 text-sm">
                  <TriangleAlert className="mt-0.5 size-4 shrink-0 text-unjudged" aria-hidden />
                  <span>{section.note}</span>
                </p>
              ) : null}
              <ul className="m-0 list-none p-0">
                {shownClaims(section.claims, all.has(section.id)).map((claim) => (
                  <ClaimRow key={claim.id} claim={claim} onSteer={steer} />
                ))}
              </ul>
              {section.claims.length > SHOWN && !all.has(section.id) ? (
                <button
                  type="button"
                  onClick={() => setAll((open) => new Set(open).add(section.id))}
                  className="mx-4 mb-3 rounded-md px-1 py-0.5 text-sm text-ink-2 hover:text-foreground focus-visible:outline-2 focus-visible:outline-tint"
                >
                  Show {section.claims.length - shownClaims(section.claims, false).length} more
                </button>
              ) : null}
            </Card>
          ))}
          {shown.priorities.length || shown.sections.length ? (
            <Card aria-label="What matters most to test">
              <CardHead title="What matters most to test" />
              <div className="py-3">
                <Priorities priorities={shown.priorities} onSteer={steer} />
              </div>
            </Card>
          ) : null}
          {sections.length || card.summary ? null : empty("Nothing learned yet")}
        </div>
      );
      break;
    case "areas":
      panel = progress ? (
        <LearnedAreas
          progress={progress}
          busy={working}
          onGoOn={onLearn}
          onLearnArea={onLearnArea}
        />
      ) : (
        empty("No areas yet")
      );
      break;
    default:
      panel = shown.starters.length ? (
        <Suggested understanding={shown} onSteer={steer} onAdd={onAdd} />
      ) : (
        empty("No suggested tests yet")
      );
  }

  return (
    <Screen>
      <ScreenHead
        title={`What ${card.name} does`}
        sub={card.one_line || undefined}
        chips={
          card.stack.length || status ? (
            <>
              {card.stack.map((entry) => (
                <Chip key={entry}>{entry}</Chip>
              ))}
              {status}
            </>
          ) : undefined
        }
        actions={
          <>
            {shown.written_at ? (
              <span className="text-sm text-ink-3">Learned {ago(shown.written_at)}</span>
            ) : null}
            {copied ? <span className="text-sm text-ink-3">{copied}</span> : null}
            {learnButton}
            <MoreMenu
              label="More"
              actions={[{ label: "Copy card", onClick: () => void copyCard() }]}
            />
          </>
        }
      />
      <div className="grid grid-cols-4 gap-5 max-lg:grid-cols-2">
        <Figure
          value={String(numbers.tools)}
          label="tools"
          sub={numbers.tools ? `${numbers.tools_seen} seen in use` : undefined}
        />
        <Figure
          value={String(numbers.state_kinds.length)}
          label="kinds of state"
          sub={numbers.state_kinds.join(" · ") || undefined}
        />
        <Figure
          value={numbers.ask_seconds === null ? "—" : secondsText(numbers.ask_seconds)}
          label="typical ask"
        />
        <Figure value={String(numbers.tests)} label="tests" sub={tests} />
      </div>
      {activity}
      <LeftOut profile={shown.profile} progress={progress} />
      <OutOfDate target={target} knowledge={shown.knowledge} onUpdated={() => void reload()} />
      <LastChanges knowledge={shown.knowledge} onUndo={undo} />
      {nodes.length ? (
        <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
          <Card role="region" aria-label="Overview">
            <CardHead title="How it works" />
            <div className="p-5 max-md:p-4">
              {isFirstPass(shown) ? <FirstPass basis={shown.overview?.basis ?? null} /> : null}
              <DiagramView
                diagram={shown.diagram}
                name={card.name}
                selected={picked?.id ?? null}
                onSelect={(id) => setNode(id)}
              />
            </div>
          </Card>
          {picked ? <NodeCheck key={picked.id} node={picked} onSteer={steer} /> : null}
        </div>
      ) : null}
      {error ? <p className="m-0 text-sm text-fail">{error}</p> : null}
      <div
        role="tablist"
        aria-label="What it learned"
        className="flex gap-[22px] overflow-x-auto border-b border-rule"
      >
        {tabs.map((entry) => (
          <button
            key={entry.id}
            type="button"
            role="tab"
            id={`learned-tab-${entry.id}`}
            aria-selected={entry.id === current}
            aria-controls="learned-panel"
            onClick={() => setTab(entry.id)}
            className={cn(
              "-mb-px inline-flex h-11 shrink-0 items-center gap-1.5 border-b-2 font-medium whitespace-nowrap focus-visible:outline-2 focus-visible:outline-tint",
              entry.id === current
                ? "border-tint text-ink"
                : "border-transparent text-ink-2 hover:text-ink",
            )}
          >
            {entry.label}
            {entry.n ? <span className="font-mono text-[12px] text-ink-3">{entry.n}</span> : null}
          </button>
        ))}
      </div>
      <div role="tabpanel" id="learned-panel" aria-labelledby={`learned-tab-${current}`}>
        {panel}
      </div>
      {connection}
    </Screen>
  );
}

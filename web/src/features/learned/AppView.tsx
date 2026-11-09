import { TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import type { AgentSession, Steer, Understanding } from "../../lib/schema";
import { POLL_MS, ready, useLoad, useVisiblePoll } from "../../lib/use-load";
import { Button } from "../../ui/button";
import { Skeleton } from "../../ui/field";
import { Connection } from "../connect/Connection";
import { Fold, Group } from "../shared/group";
import { OutOfDate } from "../shared/KnowledgeUpdate";
import { Centered, Page, Toolbar, ToolbarTitle } from "../shared/layout";
import { ClaimText } from "./ClaimText";
import { DiagramView } from "./Diagram";
import { LastChanges } from "./LastChanges";
import { Learning } from "./Learning";
import { NodeCheck } from "./NodeCheck";
import { Paths } from "./Paths";
import { Priorities } from "./Priorities";
import { ProjectCard } from "./ProjectCard";
import { SECTION, SectionHeading } from "./SectionHeading";
import { Suggested } from "./Suggested";
import { apply } from "./steer";

/**
 * What it learned: the project card, what the test writer learned of the app (how it works, its
 * usual paths, claims to check, what matters most), the tests it suggests, and the connection.
 * Every check is sent as a steer and shows at once.
 */
/** The sections a person reads: the overview's. An area's section and the Details are the
 * test writer's notes, kept for it, not shown. */
const forPeople = (section: { id: string }) =>
  !section.id.startsWith("area-") && section.id !== "details" && section.id !== "what_it_is";

/** Claims a section shows before "Show more". */
const SHOWN = 5;

/** A section's first claims, and any flagged one (it wants the owner); all of them when opened. */
function shownClaims<T extends { flagged?: boolean }>(claims: T[], all: boolean): T[] {
  return all ? claims : claims.filter((claim, index) => index < SHOWN || claim.flagged);
}

export function AppView({
  target,
  agent,
  onLearn,
  onAdd,
  onSaved,
  onRemoved,
}: {
  target: string;
  /** The test writer's thread, while it may be learning this app. */
  agent: AgentSession | null;
  onLearn: () => void;
  onAdd: (starters: string[]) => Promise<void>;
  onSaved: () => Promise<unknown>;
  onRemoved: () => void;
}) {
  const [load, reload] = useLoad(`understanding:${target}`, () => api.understanding(target));
  const [shown, setShown] = useState<Understanding | null>(null);
  const [node, setNode] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [all, setAll] = useState<Set<string>>(() => new Set());
  const loaded = ready(load);
  useEffect(() => setShown(loaded), [loaded]);

  // While the writer works, what it learns shows up here.
  const working = agent?.status === "working" && agent.target === target;
  const messages = agent?.messages.length ?? 0;
  useVisiblePoll(reload, POLL_MS, working);
  useEffect(() => {
    if (messages) void reload();
  }, [messages, reload]);

  // Opening the page is seeing what changed; a claim that wants the owner comes into view.
  const unseen = loaded?.knowledge.last ?? null;
  useEffect(() => {
    if (!unseen) return;
    void api.sawKnowledge(target).catch(() => undefined);
    if (unseen.flagged) {
      document.querySelector("[data-flagged]")?.scrollIntoView({ block: "center" });
    }
  }, [unseen, target]);

  const undo = () => {
    setError(null);
    api.undoKnowledge(target).then(setShown, () => setError("Couldn't undo that. Try again."));
  };

  const steer = (next: Steer) => {
    if (!shown) return;
    const before = shown;
    setShown(apply(shown, next));
    setError(null);
    api.steer(target, next).then(
      (answer) => {
        if (answer) setShown(answer);
      },
      () => {
        setShown(before);
        setError("Couldn't save that. Try again.");
      },
    );
  };

  const toolbar = (
    <Toolbar>
      <ToolbarTitle title="What it learned" />
      <Button variant="secondary" disabled={working} onClick={onLearn}>
        {shown?.version ? "Learn again" : "Learn"}
      </Button>
    </Toolbar>
  );
  const connection = (
    <Fold title="Connection">
      <Connection target={target} onSaved={onSaved} onRemoved={onRemoved} />
    </Fold>
  );
  if (load.status === "loading") {
    return (
      <Page>
        {toolbar}
        <Skeleton className="h-48 rounded-xl" />
        <Skeleton className="h-80 rounded-xl" />
      </Page>
    );
  }
  if (load.status !== "ready" || !shown) {
    return (
      <Page>
        {toolbar}
        <Centered>Couldn't load what it learned.</Centered>
        {connection}
      </Page>
    );
  }
  const activity = working ? <Learning session={working ? agent : null} /> : null;
  // Before the writer learns anything, the server sends only the card, at version 0.
  if (shown.version === 0) {
    return (
      <Page>
        {toolbar}
        <ProjectCard card={shown.card} target={target} learning={working} />
        {activity}
        {connection}
      </Page>
    );
  }

  const picked = shown.diagram.nodes.find((entry) => entry.id === node) ?? null;
  return (
    <Page>
      {toolbar}
      <ProjectCard card={shown.card} target={target} learning={working} />
      {activity}
      <OutOfDate target={target} knowledge={shown.knowledge} onUpdated={setShown} />
      <LastChanges knowledge={shown.knowledge} onUndo={undo} />

      <Group>
        {shown.diagram.nodes.length ? (
          <div className={SECTION}>
            <SectionHeading>How it works</SectionHeading>
            <DiagramView
              diagram={shown.diagram}
              name={shown.card.name}
              selected={node}
              onSelect={(id) => setNode((now) => (now === id ? null : id))}
            />
            {picked ? <NodeCheck node={picked} /> : null}
          </div>
        ) : null}

        {shown.paths.length ? (
          <div className={SECTION}>
            <SectionHeading>Typical paths</SectionHeading>
            <Paths paths={shown.paths} />
          </div>
        ) : null}

        {shown.card.summary || shown.card.one_line ? (
          <div className={SECTION}>
            <SectionHeading>What it is</SectionHeading>
            <p className="mx-[18px] mt-1 mb-1.5 max-w-[68ch] text-[14px] leading-relaxed">
              {shown.card.summary || shown.card.one_line}
            </p>
          </div>
        ) : null}
        {shown.sections.filter(forPeople).map((section) => (
          <div key={section.id} className={SECTION}>
            <SectionHeading>{section.title}</SectionHeading>
            {section.note ? (
              <div className="mx-[18px] mt-1.5 mb-1 flex items-start gap-2.5 rounded-[10px] bg-unjudged-soft px-3 py-2.5 text-[14px]">
                <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-warning" aria-hidden />
                <span>{section.note}</span>
              </div>
            ) : null}
            {shownClaims(section.claims, all.has(section.id)).map((claim) => (
              <div
                key={claim.id}
                data-flagged={claim.flagged ? "" : undefined}
                className="px-[18px] py-[9px] hover:bg-sunk/50"
              >
                <ClaimText text={claim.text} item={claim} />
              </div>
            ))}
            {section.claims.length > SHOWN && !all.has(section.id) ? (
              <button
                type="button"
                onClick={() => setAll((open) => new Set(open).add(section.id))}
                className="mx-[18px] mt-1 rounded-md px-1 py-0.5 text-[13px] text-ink-2 hover:text-foreground focus-visible:outline-2 focus-visible:outline-tint"
              >
                Show {section.claims.length - shownClaims(section.claims, false).length} more
              </button>
            ) : null}
          </div>
        ))}

        {shown.priorities.length || shown.sections.length ? (
          <div className={SECTION}>
            <SectionHeading>What matters most to test</SectionHeading>
            <Priorities priorities={shown.priorities} onSteer={steer} />
          </div>
        ) : null}
      </Group>

      {error ? <p className="m-0 text-[13px] text-fail">{error}</p> : null}
      {shown.starters.length ? (
        <Suggested understanding={shown} onSteer={steer} onAdd={onAdd} />
      ) : null}
      {connection}
    </Page>
  );
}

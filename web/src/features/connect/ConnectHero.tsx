import {
  BookOpen,
  Folder,
  GitBranch,
  GitFork,
  GitPullRequest,
  Globe,
  Monitor,
  Network,
} from "lucide-react";
import { type ReactNode, useState } from "react";
import { Button } from "../../ui/button";
import { Logo } from "../shell/Logo";
import { lastPart } from "./recent-folders";
import { EXAMPLES, SOURCE_WORDS, type SourceKind, sourceKind } from "./source";

const ICONS: Record<SourceKind, typeof Folder> = {
  folder: Folder,
  repo: GitFork,
  pull: GitPullRequest,
  ref: GitBranch,
  address: Globe,
};

/** The icon for what the field names, labelled with it. */
export function SourceIcon({
  kind,
  className,
  hidden,
}: {
  kind: SourceKind;
  className?: string;
  /** Beside words that say it already. */
  hidden?: boolean;
}) {
  const Icon = ICONS[kind];
  return hidden ? (
    <Icon aria-hidden className={className ?? "size-[18px]"} />
  ) : (
    <Icon role="img" aria-label={SOURCE_WORDS[kind]} className={className ?? "size-[18px]"} />
  );
}

/**
 * Connect an app: one field that takes a folder, a GitHub repository, a pull request or an
 * address, with recent folders and an example of each under it. Details holds the technical form.
 */
export function ConnectHero({
  title,
  recent,
  busy,
  error,
  onStart,
  details,
}: {
  title: string;
  recent: readonly string[];
  busy: boolean;
  error: string | null;
  onStart: (source: string) => void;
  /** The technical form, folded. */
  details: ReactNode;
}) {
  const [source, setSource] = useState("");
  const kind = sourceKind(source);
  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <div className="mx-auto flex min-h-[calc(100%-60px)] w-full max-w-[680px] flex-col items-center justify-center gap-[22px] px-6 py-10 text-center max-md:px-4">
        <Logo className="size-14 shrink-0" />
        <h1 className="m-0 text-[40px] leading-[1.15] font-semibold tracking-[-0.025em] max-md:text-[30px]">
          {title}
        </h1>
        <form
          className="flex h-14 w-full items-center gap-2 rounded-[14px] border border-rule-strong bg-group ps-[18px] pe-2 text-[17px] shadow-[0_2px_10px_var(--shadow)] focus-within:border-tint focus-within:shadow-[0_0_0_3px_var(--tint-soft)]"
          data-kind={kind}
          onSubmit={(event) => {
            event.preventDefault();
            if (source.trim()) onStart(source.trim());
          }}
        >
          <span className="text-ink-3">
            <SourceIcon kind={kind} />
          </span>
          <input
            id="source"
            value={source}
            onChange={(event) => setSource(event.target.value)}
            aria-label="Folder, GitHub repository or address"
            placeholder="~/code/my-app, owner/repo#412 or http://localhost:3000"
            autoComplete="off"
            spellCheck={false}
            // biome-ignore lint/a11y/noAutofocus: the screen is this one field.
            autoFocus
            className="h-full min-w-0 flex-1 border-0 bg-transparent text-start outline-none placeholder:text-ink-3"
          />
          <Button
            type="submit"
            size="lg"
            className="h-[42px] rounded-[10px]"
            disabled={!source.trim() || busy}
          >
            Connect
          </Button>
        </form>
        <div className="flex flex-wrap justify-center gap-2">
          {recent.map((folder) => (
            <Example
              key={folder}
              kind="folder"
              title={folder}
              onClick={() => {
                setSource(folder);
                onStart(folder);
              }}
            >
              {lastPart(folder)}
            </Example>
          ))}
          {EXAMPLES.map((example) => (
            <Example
              key={example.text}
              kind={sourceKind(example.fill)}
              title={example.fill}
              onClick={() => {
                setSource(example.fill);
                document.getElementById("source")?.focus();
              }}
            >
              {example.text}
            </Example>
          ))}
        </div>
        {error ? (
          <p role="alert" className="m-0 text-sm text-fail">
            {error}
          </p>
        ) : null}
        <div className="mt-2.5 grid w-full grid-cols-3 gap-3.5 max-md:grid-cols-1">
          {[
            [BookOpen, "Reads the code"],
            [Monitor, "Builds a test copy"],
            [Network, "Maps what it does"],
          ].map(([Icon, words]) => {
            const Shown = Icon as typeof BookOpen;
            return (
              <div
                key={words as string}
                className="flex flex-col items-center gap-2.5 rounded-[14px] bg-sunk px-2.5 py-[18px] text-sm text-ink-2"
              >
                <Shown aria-hidden className="size-[18px]" />
                {words as string}
              </div>
            );
          })}
        </div>
        <div className="w-full text-start">{details}</div>
      </div>
    </div>
  );
}

function Example({
  kind,
  title,
  onClick,
  children,
}: {
  kind: SourceKind;
  title: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      className="inline-flex h-9 items-center gap-2 rounded-full border border-rule bg-group px-[13px] font-mono text-[13px] text-ink-2 hover:border-tint hover:text-tint-ink focus-visible:outline-2 focus-visible:outline-tint"
    >
      <SourceIcon kind={kind} className="size-[15px]" hidden />
      {children}
    </button>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorText } from "../../lib/api";
import type { AgentSession, Discovery, TargetDetail } from "../../lib/schema";
import { ready as loaded, useLoad } from "../../lib/use-load";
import { Button } from "../../ui/button";
import { Input } from "../../ui/field";
import { Progress } from "../environment/Progress";
import { TestEnvironment } from "../environment/TestEnvironment";
import { UiOffer } from "../environment/UiOffer";
import type { Standing } from "../environment/words";
import { Fold, Group, TextLink } from "../shared/group";
import { Page, Toolbar, ToolbarTitle } from "../shared/layout";
import { Composer } from "../tests/Composer";
import { ModelStep } from "./ModelStep";
import { Probe } from "./Probe";
import { lastPart, recentFolders, rememberFolder } from "./recent-folders";
import { SetupStep } from "./SetupStep";
import { Steps } from "./Steps";
import { TargetFields } from "./TargetFields";
import { TracesHelp } from "./TracesHelp";

const POLL_MS = 700;

const UNREACHABLE = "Couldn't reach evalution. Check that it's still running, then try again.";

/**
 * Connect an app, from nothing to its first test: its address or project folder, the model, one
 * real message, once it is learned the copy tests run on, then the test. evalution looks at the app and says
 * what it found, asks what it couldn't tell, and each step that fails says what to do next. Details
 * holds the technical form.
 */
export function ConnectView({
  first,
  onReady,
  onLearn,
  onLearned,
  writer,
  onFirstTest,
  onAsk,
}: {
  first: boolean;
  /** The app is saved: the page follows it, on this screen. */
  onReady: (name: string) => Promise<unknown>;
  /** There's a model to learn it with. */
  onLearn: (name: string) => void;
  onLearned: () => void;
  /** The test writer's thread: it learns the app, and says when it's done. */
  writer?: AgentSession | null;
  /** The test copy is proven: on to the tests. */
  onFirstTest?: () => void;
  onAsk: (text: string) => Promise<void>;
}) {
  const [title] = useState(first ? "Connect your first app" : "Connect an app");
  const [recent] = useState(recentFolders);
  const [path, setPath] = useState("");
  const [job, setJob] = useState<Discovery | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [made, setMade] = useState<TargetDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [modelChosen, setModelChosen] = useState(false);
  const chooseModel = useCallback((chosen: boolean) => setModelChosen(chosen), []);
  const accepting = useRef(false);
  const name = made?.name ?? null;

  const [targets, reloadTargets] = useLoad(name ? `setup:${name}` : null, api.targets);
  const row = loaded(targets)?.find((entry) => entry.name === name) ?? null;

  // Learning takes a model: it starts as soon as there's both an app and one.
  const learning = useRef(false);
  useEffect(() => {
    if (name && modelChosen && !learning.current) {
      learning.current = true;
      onLearn(name);
    }
  }, [name, modelChosen, onLearn]);

  const checkTraces = async (jobId: string) => {
    setChecking(true);
    try {
      setJob(await api.checkTraces(jobId));
    } catch (failure) {
      setError(errorText(failure, UNREACHABLE));
    }
    setChecking(false);
  };

  const start = async (folder: string) => {
    if (!folder.trim()) return;
    setError(null);
    setMade(null);
    learning.current = false;
    setAnswers({});
    setBusy(true);
    try {
      setJob(await api.discover(folder.trim()));
      rememberFolder(folder.trim());
    } catch (failure) {
      setError(errorText(failure, UNREACHABLE));
    }
    setBusy(false);
  };

  const finish = async (save: () => Promise<TargetDetail>) => {
    if (accepting.current) return;
    accepting.current = true;
    setError(null);
    setBusy(true);
    try {
      const saved = await save();
      setMade(saved);
      await onReady(saved.name);
    } catch (failure) {
      setError(errorText(failure, "Couldn't save it. Try again."));
    }
    accepting.current = false;
    setBusy(false);
  };

  // Follow the folder until it is read; when nothing is left to ask, save it right away.
  const id = job?.id;
  const status = job?.status;
  const asks = job?.questions.length ?? 0;
  // Traces not reaching evalution yet: show how to send them before connecting.
  const waiting = !!job?.traces && !job.traces.arriving;
  useEffect(() => {
    if (!id || status !== "working") return;
    const timer = window.setInterval(() => {
      api.discovery(id).then(setJob, () => setError(UNREACHABLE));
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [id, status]);
  const finishRef = useRef(finish);
  finishRef.current = finish;
  useEffect(() => {
    if (id && status === "ready" && !asks && !waiting)
      void finishRef.current(() => api.accept(id, {}));
  }, [id, status, asks, waiting]);

  const looking = job !== null && job.status === "working";
  const asking = job?.status === "ready" && asks > 0 && !made;
  const answered = job?.questions.every((q) => answers[q.key]?.trim()) ?? false;
  const appState = made
    ? "done"
    : job?.status === "failed" || (error && !looking)
      ? "problem"
      : "now";
  const later = made ? "now" : "waiting";

  // Learning ends when the writer, seen working on this app, stops: then the test copy is set up.
  const learningNow = !!name && writer?.target === name && writer.status === "working";
  const [sawLearning, setSawLearning] = useState(false);
  useEffect(() => {
    if (learningNow) setSawLearning(true);
  }, [learningNow]);
  const learned = !!made && modelChosen && sawLearning && !learningNow;
  const [copy, setCopy] = useState<Standing | null>(null);
  const proven = copy === "proven";

  return (
    <>
      <Toolbar>
        <ToolbarTitle title={title} />
      </Toolbar>
      <Page narrow>
        {made ? (
          <Progress
            steps={[
              { label: "Connect", state: "done" },
              { label: "Learn", state: learned ? "done" : modelChosen ? "now" : "waiting" },
              { label: "Set up testing", state: proven ? "done" : learned ? "now" : "waiting" },
              { label: "First test", state: proven ? "now" : "waiting" },
            ]}
          />
        ) : null}
        <SetupStep n={1} title="App" state={appState}>
          <form
            className="flex flex-col gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              void start(path);
            }}
          >
            <label htmlFor="folder" className="text-[13px] font-medium">
              Address or project folder
            </label>
            <div className="flex gap-2">
              <Input
                id="folder"
                value={path}
                onChange={(event) => setPath(event.target.value)}
                placeholder="~/code/my-app or http://localhost:3000/chat"
                autoComplete="off"
                spellCheck={false}
                autoFocus
                className="h-9"
              />
              <Button type="submit" size="lg" className="h-9" disabled={!path.trim() || looking}>
                Connect
              </Button>
            </div>
            {recent.length && !job ? (
              <div className="flex flex-wrap gap-1.5 pt-1">
                {recent.map((folder) => (
                  <Button
                    key={folder}
                    variant="secondary"
                    size="xs"
                    title={folder}
                    onClick={() => {
                      setPath(folder);
                      void start(folder);
                    }}
                  >
                    {lastPart(folder)}
                  </Button>
                ))}
              </div>
            ) : null}
          </form>

          {job ? (
            <Group className="flex flex-col gap-4 px-4 py-4">
              <Steps
                steps={[
                  ...job.steps,
                  ...(made
                    ? [
                        { text: "Ready", state: "done" as const },
                        ...(modelChosen
                          ? [
                              learned
                                ? { text: "Learned your app", state: "done" as const }
                                : { text: "Learning your app…", state: "doing" as const },
                            ]
                          : []),
                      ]
                    : []),
                ]}
              />
              {made && modelChosen && !busy ? (
                <TextLink className="w-fit text-[13px]" onClick={onLearned}>
                  What it learned
                </TextLink>
              ) : null}
              {asking ? (
                <form
                  className="flex flex-col gap-3 border-separator border-t pt-4"
                  onSubmit={(event) => {
                    event.preventDefault();
                    if (answered) void finish(() => api.accept(job.id, { answers }));
                  }}
                >
                  {job.questions.map((question) => (
                    <div key={question.key} className="flex flex-col gap-1.5 text-[13px]">
                      <label htmlFor={`question-${question.key}`}>{question.text}</label>
                      <Input
                        id={`question-${question.key}`}
                        value={answers[question.key] ?? ""}
                        onChange={(event) =>
                          setAnswers((now) => ({ ...now, [question.key]: event.target.value }))
                        }
                        placeholder={question.hint}
                        autoComplete="off"
                      />
                    </div>
                  ))}
                  <Button type="submit" className="self-end" disabled={!answered || busy}>
                    Connect
                  </Button>
                </form>
              ) : null}
              {job.status === "ready" && waiting && job.traces && !made ? (
                <>
                  <TracesHelp
                    traces={job.traces}
                    checking={checking}
                    onCheck={() => void checkTraces(job.id)}
                  />
                  {asks ? null : (
                    <Button
                      className="self-end"
                      disabled={busy}
                      onClick={() => void finish(() => api.accept(job.id, {}))}
                    >
                      Connect
                    </Button>
                  )}
                </>
              ) : null}
            </Group>
          ) : null}

          {error ? <p className="m-0 text-[12.5px] text-destructive">{error}</p> : null}

          {made ? (
            <p className="m-0 text-[13px] text-ink-2">Connected as {made.adapter_label}.</p>
          ) : null}

          <Fold title="Details">
            <TargetFields
              key={made ? `made:${made.name}` : job?.target ? job.id : "blank"}
              saved={made ?? job?.target ?? undefined}
              fresh={!made}
              action={made ? "Save" : "Connect"}
              onSave={async (body) => {
                if (made) {
                  setMade(await api.updateTarget(body));
                  await reloadTargets();
                  return;
                }
                await finish(() =>
                  job?.status === "ready"
                    ? api.accept(job.id, { target: body })
                    : api.connect(body),
                );
              }}
            />
          </Fold>
        </SetupStep>

        <SetupStep n={2} title="Model" state={modelChosen ? "done" : "now"}>
          <ModelStep onChosen={chooseModel} />
        </SetupStep>

        <SetupStep n={3} title="Test message" state={row?.checked ? "done" : later}>
          {name ? <Probe target={name} checked={!!row?.checked} onChange={reloadTargets} /> : null}
        </SetupStep>

        <SetupStep
          n={4}
          title="Set up testing"
          state={learned ? (proven ? "done" : "now") : "waiting"}
        >
          {name ? (
            <TestEnvironment
              target={name}
              mode="setup"
              onStanding={setCopy}
              onFirstTest={onFirstTest}
              onAsk={onAsk}
            />
          ) : null}
        </SetupStep>

        <SetupStep n={5} title="First test" state={made && modelChosen ? "now" : "waiting"}>
          {name ? (
            <div className="flex flex-col gap-5">
              <UiOffer
                // Read again once the copy is proven: the agent can learn the page from then.
                key={proven ? "proven" : "not yet"}
                target={name}
                onAsk={onAsk}
                className="rounded-[14px] border border-border bg-group p-5 max-md:p-4"
              />
              <Composer target={name} onAsk={onAsk} />
            </div>
          ) : null}
        </SetupStep>
      </Page>
    </>
  );
}

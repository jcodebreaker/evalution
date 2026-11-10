import { useCallback, useEffect, useRef, useState } from "react";
import { api, appApi, errorText } from "../../lib/api";
import type { AgentSession, DiscoveryView, TargetDetail } from "../../lib/schema";
import { ready as loaded, useLoad } from "../../lib/use-load";
import { Fold } from "../shared/group";
import { ConnectHero } from "./ConnectHero";
import { recentFolders, rememberFolder } from "./recent-folders";
import { SetupLive } from "./SetupLive";
import { TargetFields } from "./TargetFields";

const POLL_MS = 700;

const UNREACHABLE = "Couldn't reach evalution. Check that it's still running, then try again.";

/**
 * Connect an app, from nothing to its first test: one field for its folder, repository, pull
 * request or address; then one live setup: evalution reads the code, sends a real message, makes
 * the copy tests run on and learns the app, asking only what it couldn't tell. Details holds the
 * technical form.
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
  const [job, setJob] = useState<DiscoveryView | null>(null);
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
      setJob(await appApi.checkTraces(jobId));
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
      setJob(await appApi.discover(folder.trim()));
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
      appApi.discovery(id).then(setJob, () => setError(UNREACHABLE));
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [id, status]);
  const finishRef = useRef(finish);
  finishRef.current = finish;
  useEffect(() => {
    if (id && status === "ready" && !asks && !waiting)
      void finishRef.current(() => api.accept(id, {}));
  }, [id, status, asks, waiting]);

  // Learning ends when the writer, seen working on this app, stops.
  const learningNow = !!name && writer?.target === name && writer.status === "working";
  const [sawLearning, setSawLearning] = useState(false);
  useEffect(() => {
    if (learningNow) setSawLearning(true);
  }, [learningNow]);
  const learned = !!made && modelChosen && sawLearning && !learningNow;

  const details = (
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
            job?.status === "ready" ? api.accept(job.id, { target: body }) : api.connect(body),
          );
        }}
      />
    </Fold>
  );

  if (!job && !made) {
    return (
      <ConnectHero
        title={title}
        recent={recent}
        busy={busy}
        error={error}
        onStart={(source) => void start(source)}
        details={details}
      />
    );
  }

  return (
    <SetupLive
      job={job}
      made={made}
      row={row}
      busy={busy}
      error={error}
      checking={checking}
      answers={answers}
      onAnswers={setAnswers}
      onAnswer={() => {
        if (job) void finish(() => api.accept(job.id, { answers }));
      }}
      onAccept={() => {
        if (job) void finish(() => api.accept(job.id, {}));
      }}
      onCheckTraces={() => {
        if (job) void checkTraces(job.id);
      }}
      onReloadTargets={reloadTargets}
      onAgain={() => {
        setJob(null);
        setError(null);
      }}
      modelChosen={modelChosen}
      onModelChosen={chooseModel}
      writer={writer?.target === name ? (writer ?? null) : null}
      learning={learningNow}
      learned={learned}
      details={details}
      onLearned={onLearned}
      onFirstTest={onFirstTest}
      onAsk={onAsk}
    />
  );
}

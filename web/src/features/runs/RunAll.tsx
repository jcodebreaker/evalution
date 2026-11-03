import { Play } from "lucide-react";
import type { Scope } from "../../lib/api";
import { api } from "../../lib/api";
import { usd } from "../../lib/format";
import { useLoad } from "../../lib/use-load";
import { Button } from "../../ui/button";
import { Spinner } from "../../ui/loading";

/**
 * Run all (or `cases`, under `label`), with what it will cost once the server has estimated it;
 * while a run is going, the way to it instead.
 */
export function RunAll({
  scope,
  tests,
  cases,
  label = "Run all",
  stamp,
  going,
  onGoing,
  onRun,
}: {
  scope: Scope;
  tests: number;
  cases?: readonly string[];
  label?: string;
  /** Re-estimate when this changes (a new run makes the costs better known). */
  stamp: string;
  going: boolean;
  onGoing: () => void;
  onRun: () => void;
}) {
  const [estimate] = useLoad(
    going || !tests
      ? null
      : `estimate:${scope.target}:${scope.set}:${tests}:${stamp}:${cases?.join() ?? ""}`,
    () => api.estimate(scope, undefined, cases),
  );
  if (going) {
    return (
      <Button variant="secondary" onClick={onGoing}>
        <Spinner />
        Running
      </Button>
    );
  }
  return (
    <Button onClick={onRun}>
      <Play />
      {estimate.status === "ready" ? `${label} · about ${usd(estimate.data.usd)}` : label}
    </Button>
  );
}

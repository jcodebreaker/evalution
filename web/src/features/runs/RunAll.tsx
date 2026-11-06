import { Play } from "lucide-react";
import { Button } from "../../ui/button";
import { Spinner } from "../../ui/loading";

/** Run all (or `cases`, under `label`); while a run is going, the way to it instead. */
export function RunAll({
  label = "Run all",
  going,
  onGoing,
  onRun,
}: {
  label?: string;
  going: boolean;
  onGoing: () => void;
  onRun: () => void;
}) {
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
      {label}
    </Button>
  );
}

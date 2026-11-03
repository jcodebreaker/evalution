import { useState } from "react";
import type { Discovery } from "../../lib/schema";
import { Button } from "../../ui/button";
import { Spinner } from "../../ui/loading";

function Copyable({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard.writeText(text).then(
      () => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      },
      () => setCopied(false),
    );
  };
  return (
    <div className="relative">
      <pre className="m-0 overflow-x-auto rounded-[7px] bg-window px-2.5 py-2 pe-16 font-mono text-[11.5px] leading-[1.55]">
        {text}
      </pre>
      <Button
        variant="secondary"
        size="xs"
        className="absolute top-1.5 right-1.5"
        aria-label={label}
        onClick={copy}
      >
        {copied ? "Copied" : "Copy"}
      </Button>
    </div>
  );
}

/** The lines that send the app's traces to evalution, and a check that they arrive. */
export function TracesHelp({
  traces,
  checking,
  onCheck,
}: {
  traces: NonNullable<Discovery["traces"]>;
  checking: boolean;
  onCheck: () => void;
}) {
  return (
    <div className="flex flex-col gap-2.5 border-separator border-t pt-4 text-[13px]">
      <p className="m-0 text-ink-2">{traces.text}</p>
      <Copyable text={traces.lines} label="Copy the lines" />
      {traces.command ? (
        <>
          <p className="m-0 text-ink-2">Then run, in the project folder:</p>
          <Copyable text={traces.command} label="Copy the command" />
        </>
      ) : null}
      <Button variant="secondary" className="self-start" disabled={checking} onClick={onCheck}>
        {checking ? <Spinner /> : null}
        Check again
      </Button>
    </div>
  );
}

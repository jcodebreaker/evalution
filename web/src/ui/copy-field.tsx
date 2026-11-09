import { useState } from "react";
import { copy } from "../lib/clipboard";
import { Button } from "./button";
import { Input } from "./field";

/** A value to take elsewhere: shown whole, selected on focus, with a Copy button. */
export function CopyField({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <span className="flex min-w-0 items-center gap-2">
      <Input
        readOnly
        value={value}
        aria-label={label}
        onFocus={(event) => event.target.select()}
        className="font-mono text-[12.5px]"
      />
      <Button
        variant="secondary"
        size="sm"
        onClick={() =>
          void copy(value).then((done) => {
            setCopied(done);
            if (done) window.setTimeout(() => setCopied(false), 1500);
          })
        }
      >
        {copied ? "Copied" : "Copy"}
      </Button>
    </span>
  );
}

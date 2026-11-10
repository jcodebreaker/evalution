import { Check, X } from "lucide-react";
import { useState } from "react";
import type { DiagramNode, Steer } from "../../lib/schema";
import { Button } from "../../ui/button";
import { Input } from "../../ui/field";
import { Card, CardHead, Chip } from "../connect/kit";
import { EVIDENCE_LABEL } from "./evidence";

/** The box picked in the diagram, beside it: what it is, where that comes from, and whether
 * it's right (right, wrong, or a fix in the owner's words), sent as a steer. */
export function NodeCheck({
  node,
  onSteer,
}: {
  node: DiagramNode;
  onSteer: (steer: Steer) => void;
}) {
  const [fixing, setFixing] = useState(false);
  const [fix, setFix] = useState("");
  const out = node.flagged || node.stale === "direct";
  const near = !out && node.stale === "indirect";
  return (
    <Card aria-label={`About ${node.title}`}>
      <CardHead title={node.fix ?? node.title}>
        {out ? <Chip tone="warn">May be out of date</Chip> : null}
        {near ? <Chip tone="warn">Related code changed</Chip> : null}
        {node.seen ? null : <Chip>Not seen in use</Chip>}
      </CardHead>
      <div className="flex flex-col gap-4 p-5 max-md:p-4">
        {node.sub ? <p className="m-0">{node.sub}</p> : null}
        {node.evidence.length ? (
          <div className="flex flex-col gap-2">
            <h3 className="m-0 text-[13px] font-medium text-ink-3">From</h3>
            <div className="flex flex-wrap gap-1.5">
              {node.evidence.map((entry, index) => (
                <span
                  key={index}
                  className="inline-flex h-[22px] items-center gap-1.5 rounded-md bg-sunk px-[7px] font-mono text-[12px] text-ink-2"
                >
                  {EVIDENCE_LABEL[entry.kind]} · {entry.ref}
                </span>
              ))}
            </div>
          </div>
        ) : null}
        <div className="flex flex-col gap-2">
          <h3 className="m-0 text-[13px] font-medium text-ink-3">Is this right?</h3>
          <div className="flex flex-wrap gap-2">
            <Button
              variant={node.verdict === "right" ? "default" : "secondary"}
              size="sm"
              aria-pressed={node.verdict === "right"}
              onClick={() => onSteer({ node: node.id, verdict: "right" })}
            >
              <Check />
              Right
            </Button>
            <Button
              variant={node.verdict === "wrong" ? "destructive" : "secondary"}
              size="sm"
              aria-pressed={node.verdict === "wrong"}
              onClick={() => onSteer({ node: node.id, verdict: "wrong" })}
            >
              <X />
              Wrong
            </Button>
            <Button
              variant="secondary"
              size="sm"
              aria-expanded={fixing}
              onClick={() => {
                setFix(node.fix ?? node.title);
                setFixing(!fixing);
              }}
            >
              Fix it
            </Button>
          </div>
          {fixing ? (
            <form
              className="flex gap-1.5"
              onSubmit={(event) => {
                event.preventDefault();
                if (fix.trim()) onSteer({ node: node.id, fix: fix.trim() });
                setFixing(false);
              }}
            >
              <Input
                aria-label={`What ${node.title} is`}
                value={fix}
                onChange={(event) => setFix(event.target.value)}
              />
              <Button type="submit" size="sm" disabled={!fix.trim()}>
                Save
              </Button>
            </form>
          ) : null}
        </div>
        {node.updated_from ? (
          <p className="m-0 text-sm text-ink-3">From {node.updated_from.slice(0, 7)}</p>
        ) : null}
      </div>
    </Card>
  );
}

import type { DiagramNode } from "../../lib/schema";
import type { Verb } from "./Check";
import { Check } from "./Check";
import { CodeMark, EvidenceLine } from "./evidence";

/** The box picked in the diagram, to check like a claim. */
export function NodeCheck({ node, onSteer }: { node: DiagramNode; onSteer: (verb: Verb) => void }) {
  return (
    <div className="mx-[18px] my-1.5 rounded-[10px] bg-selection-strong px-3 py-2.5">
      <Check key={node.id} item={node} label={node.title} always onSteer={onSteer}>
        <b className="block text-[13.5px] font-semibold">
          {node.fix ?? node.title}
          {node.seen ? null : (
            <span className="font-normal text-ink-2"> (not seen in real use yet)</span>
          )}
        </b>
        {node.sub ? <span className="block text-[12.5px] text-ink-2">{node.sub}</span> : null}
        <EvidenceLine evidence={node.evidence} />
        <CodeMark item={node} />
      </Check>
    </div>
  );
}

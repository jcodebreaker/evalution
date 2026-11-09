import type { DiagramNode } from "../../lib/schema";
import { CodeMark, EvidenceLine } from "./evidence";

/** The box picked in the diagram: what it is and where it was seen. */
export function NodeCheck({ node }: { node: DiagramNode }) {
  return (
    <div className="mx-[18px] my-1.5 rounded-[10px] bg-selection-strong px-3 py-2.5">
      <b className="block text-[13.5px] font-semibold">
        {node.fix ?? node.title}
        {node.seen ? null : (
          <span className="font-normal text-ink-2"> (not seen in real use yet)</span>
        )}
      </b>
      {node.sub ? <span className="block text-[13px] text-ink-2">{node.sub}</span> : null}
      <EvidenceLine evidence={node.evidence} />
      <CodeMark item={node} />
    </div>
  );
}

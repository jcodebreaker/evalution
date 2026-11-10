import { ready, useLoad } from "../../lib/use-load";
import { Skeleton as Placeholder } from "../../ui/field";
import { Card } from "../connect/kit";
import { Fold } from "../shared/group";
import { AppSkeleton, loadSkeleton, ranked } from "./AppSkeleton";

/** The app's tools from its code, the most used first: what each takes, where it is and how
 * often real use called it; everything else the code is built from is folded under it. */
export function Tools({ target }: { target: string }) {
  const [state] = useLoad(`tools:${target}`, () => loadSkeleton(target));
  const found = ready(state);
  const tools = ranked((found?.entries ?? []).filter((entry) => entry.kind === "tool"));
  return (
    <div className="flex flex-col gap-3">
      {state.status === "loading" ? (
        <Placeholder className="h-32 rounded-2xl" />
      ) : tools.length ? (
        <Card className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr className="text-[13px] text-ink-3">
                <th className="border-b border-rule px-5 py-2.5 text-start font-medium">Tool</th>
                <th className="border-b border-rule px-5 py-2.5 text-start font-medium">Takes</th>
                <th className="border-b border-rule px-5 py-2.5 text-start font-medium max-md:hidden">
                  Where
                </th>
                <th className="border-b border-rule px-5 py-2.5 text-end font-medium">Seen</th>
              </tr>
            </thead>
            <tbody>
              {tools.map((tool) => (
                <tr key={tool.id} className="border-b border-rule last:border-b-0">
                  <td className="px-5 py-3">
                    <span className="inline-flex h-6 items-center rounded-[7px] bg-k-tool-soft px-2 font-mono text-[13px] font-medium text-k-tool">
                      {tool.name}
                    </span>
                  </td>
                  <td className="px-5 py-3 font-mono text-[13px] text-ink-3">
                    {tool.args.join(", ") || "—"}
                  </td>
                  <td className="px-5 py-3 font-mono text-[13px] text-ink-3 max-md:hidden">
                    {tool.path}:{tool.line}
                  </td>
                  <td
                    className={
                      tool.uses
                        ? "px-5 py-3 text-end font-mono text-[13px]"
                        : "px-5 py-3 text-end font-mono text-[13px] text-ink-3"
                    }
                  >
                    {tool.uses === null ? "—" : `${tool.uses}×`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ) : null}
      {tools.length ? (
        <Fold title="Everything in the code">
          <AppSkeleton target={target} />
        </Fold>
      ) : (
        <AppSkeleton target={target} />
      )}
    </div>
  );
}

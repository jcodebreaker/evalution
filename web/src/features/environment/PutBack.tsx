import { Database } from "lucide-react";
import { storeKind } from "../../lib/isolation";
import type { IsolationView, StoreWords } from "../../lib/schema";
import { StatusDot } from "../../ui/status";
import { Card, CardHead, Chip } from "../connect/kit";
import { stepTime } from "./words";

type Store = NonNullable<IsolationView["plan"]>["stores"][number];

/** How a store is put back before every try, short. */
export function putBackWords(store: Store, putBack: IsolationView["put_back"]): string {
  switch (store.strategy) {
    case "relocate":
      if (store.kind === "memory") return "Cleared by a restart";
      if (store.kind === "postgres")
        return putBack?.hot === "proven"
          ? "New copy from a template, app up"
          : "New copy from a template";
      if (store.kind === "redis") return "Restarted empty";
      return "Fresh copy";
    case "snapshot":
      return "Snapshot put back";
    case "identity":
      return `Own ${store.keyed_by ?? "person"} each try`;
    default:
      return "Not put back";
  }
}

/** "What it puts back": each store, what it holds, how it is put back before every try, how
 * long that took when it was set up, and whether the last check held. Traces are left alone. */
export function PutBackTable({
  isolation,
  stores,
}: {
  isolation: IsolationView;
  stores: readonly StoreWords[];
}) {
  const plan = isolation.plan;
  if (!plan?.stores.length && !plan?.exclude.length) return null;
  const words = new Map(stores.map((store) => [store.id, store]));
  const seconds = (id: string) =>
    isolation.job?.steps.find((step) => step.key === `store:${id}`)?.seconds ?? null;
  const leaky = (store: Store) =>
    plan.leaks.some((leak) => leak.includes(store.locator) || leak.includes(store.id));
  const proven = isolation.status === "proven";
  const putBack = isolation.put_back;
  return (
    <Card>
      <CardHead title="What it puts back">
        {putBack ? (
          <Chip tone={putBack.restart ? "warn" : "ok"}>
            {putBack.restart ? "Restarts the app each try" : "App stays up"}
          </Chip>
        ) : null}
      </CardHead>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-start">
          <thead>
            <tr className="text-sm text-ink-3">
              <th className="border-b border-rule px-5 py-2.5 text-start font-medium">Store</th>
              <th className="border-b border-rule px-5 py-2.5 text-start font-medium max-md:hidden">
                Holds
              </th>
              <th className="border-b border-rule px-5 py-2.5 text-start font-medium">
                Before every try
              </th>
              <th className="border-b border-rule px-5 py-2.5 text-end font-medium">Took</th>
              <th className="border-b border-rule px-5 py-2.5 text-center font-medium">Checked</th>
            </tr>
          </thead>
          <tbody>
            {plan.stores.map((store) => {
              const named = words.get(store.id);
              const took = seconds(store.id);
              const bad = leaky(store);
              return (
                <tr key={store.id} className="border-b border-rule last:border-b-0">
                  <td className="px-5 py-3.5">
                    <span className="flex min-w-0 items-center gap-2.5" title={store.locator}>
                      <Database aria-hidden className="size-[15px] shrink-0 text-ink-3" />
                      <b className="font-medium">{storeKind(store.kind)}</b>
                      {named?.name ? (
                        <span className="truncate font-mono text-[13px] text-ink-3">
                          {named.name}
                        </span>
                      ) : null}
                    </span>
                  </td>
                  <td className="px-5 py-3.5 text-ink-3 max-md:hidden">{named?.holds ?? "—"}</td>
                  <td className="px-5 py-3.5">{putBackWords(store, putBack)}</td>
                  <td className="px-5 py-3.5 text-end font-mono text-[13px] text-ink-3">
                    {took === null ? "—" : stepTime(took)}
                  </td>
                  <td className="px-5 py-3.5 text-center">
                    {bad ? (
                      <StatusDot state="fail" label="Didn't hold" />
                    ) : proven && store.strategy !== "none" ? (
                      <StatusDot state="pass" label="Held" />
                    ) : (
                      <StatusDot state="none" label="Not checked" />
                    )}
                  </td>
                </tr>
              );
            })}
            {plan.exclude.map((left) => (
              <tr key={left} className="border-b border-rule last:border-b-0">
                <td className="px-5 py-3.5">
                  <span className="flex min-w-0 items-center gap-2.5">
                    <Database aria-hidden className="size-[15px] shrink-0 text-ink-3" />
                    <b className="truncate font-medium">{left}</b>
                  </span>
                </td>
                <td className="px-5 py-3.5 text-ink-3 max-md:hidden">what happened</td>
                <td className="px-5 py-3.5">Left alone, as evidence</td>
                <td className="px-5 py-3.5 text-end font-mono text-[13px] text-ink-3">—</td>
                <td className="px-5 py-3.5 text-center text-ink-3">—</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

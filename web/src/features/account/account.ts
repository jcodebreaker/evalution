import { createContext, useContext } from "react";
import type { Me } from "../../lib/schema";

/** Hosted and signed in: who, where, and a way to read it again after a change. */
export type Account = {
  me: Me & { user: NonNullable<Me["user"]>; workspace: NonNullable<Me["workspace"]> };
  reload: () => Promise<unknown>;
};

/** `null` locally: the page then shows nothing about accounts. */
export const AccountContext = createContext<Account | null>(null);

export const useAccount = () => useContext(AccountContext);

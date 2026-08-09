import { createContext, useContext } from "react";

import type { ActiveSession } from "../api/session";

export type AuthStatus = "checking" | "authenticated" | "anonymous";

export interface AuthContextValue {
  status: AuthStatus;
  session: ActiveSession | null;
  signIn: (email: string, password: string, secondFactorCode?: string) => Promise<void>;
  signOut: () => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (value === null) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}

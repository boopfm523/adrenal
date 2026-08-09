import { useQueryClient } from "@tanstack/react-query";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type PropsWithChildren,
} from "react";

import { ApiError, login, logout, restoreSession } from "../api/client";
import { sessionStore, type ActiveSession } from "../api/session";
import { AuthContext, type AuthStatus } from "./context";

export function AuthProvider({ children }: PropsWithChildren): React.JSX.Element {
  const queryClient = useQueryClient();
  const [session, setSession] = useState<ActiveSession | null>(sessionStore.get());
  const [status, setStatus] = useState<AuthStatus>("checking");

  useEffect(() => sessionStore.subscribe((next) => {
    setSession(next);
    if (next === null) {
      setStatus("anonymous");
      queryClient.clear();
    } else {
      setStatus("authenticated");
    }
  }), [queryClient]);

  useEffect(() => {
    let active = true;
    void restoreSession()
      .then(() => {
        if (active) setStatus("authenticated");
      })
      .catch((error: unknown) => {
        if (!active) return;
        if (!(error instanceof ApiError) || error.status !== 401) {
          // Authentication state remains safely anonymous. Route-level retries can be
          // added without exposing the server response or retaining stale health data.
          sessionStore.clear();
        }
        setStatus("anonymous");
      });
    return () => { active = false; };
  }, []);

  const signIn = useCallback(async (email: string, password: string, secondFactorCode?: string): Promise<void> => {
    await login({ email, password, ...(secondFactorCode === undefined ? {} : { second_factor_code: secondFactorCode }) });
  }, []);

  const signOut = useCallback(async (): Promise<void> => {
    await logout();
    queryClient.clear();
  }, [queryClient]);

  const value = useMemo(
    () => ({ status, session, signIn, signOut }),
    [session, signIn, signOut, status],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

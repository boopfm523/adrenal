import { useState, type SyntheticEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { ApiError } from "../api/client";
import { useAuth } from "../auth/context";

interface LocationState {
  from?: string;
}

export function LoginPage(): React.JSX.Element {
  const { status, signIn } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [secondFactorCode, setSecondFactorCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const state = location.state as LocationState | null;

  if (status === "authenticated") return <Navigate to="/today" replace />;

  async function submit(event: SyntheticEvent<HTMLFormElement, SubmitEvent>): Promise<void> {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await signIn(email, password, secondFactorCode.trim() || undefined);
      const destination = state?.from?.startsWith("/") === true ? state.from : "/today";
      void navigate(destination, { replace: true });
    } catch (caught: unknown) {
      setError(caught instanceof ApiError ? caught.message : "Sign-in is temporarily unavailable.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-page">
      <section className="login-panel" aria-labelledby="login-heading">
        <p className="eyebrow">Private health record</p>
        <h1 id="login-heading">Sign in to HealthCurve</h1>
        <p>Your session stays on this device. Health details are never placed in the page title or URL.</p>
        {error === null ? null : <div className="error-summary" role="alert" tabIndex={-1}>{error}</div>}
        <form onSubmit={(event) => { void submit(event); }}>
          <label htmlFor="email">Email</label>
          <input
            id="email"
            name="email"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(event) => { setEmail(event.currentTarget.value); }}
          />
          <label htmlFor="password">Password</label>
          <input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => { setPassword(event.currentTarget.value); }}
          />
          <label htmlFor="second-factor">Authenticator or recovery code</label>
          <input
            id="second-factor"
            name="second_factor_code"
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            spellCheck={false}
            value={secondFactorCode}
            onChange={(event) => { setSecondFactorCode(event.currentTarget.value); }}
            aria-describedby="second-factor-help"
          />
          <p id="second-factor-help" className="field-help">Required after MFA is enabled. Recovery codes also work here.</p>
          <button type="submit" disabled={submitting}>
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </section>
    </main>
  );
}

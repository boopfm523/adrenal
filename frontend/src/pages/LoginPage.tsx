import { Alert, Box, Button, Image, Paper, PasswordInput, Stack, Text, TextInput, Title } from "@mantine/core";
import { useEffect, useState, type SyntheticEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import protectiveHorizonLogo from "../../../design/logo-concepts/healthcurve-protective-horizon-concept.png";
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
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const state = location.state as LocationState | null;

  useEffect(() => {
    document.title = "Sign in · HealthCurve.ai";
  }, []);

  if (status === "authenticated") return <Navigate to="/healthcurve" replace />;

  async function submit(event: SyntheticEvent<HTMLFormElement, SubmitEvent>): Promise<void> {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await signIn(email, password);
      const destination = state?.from?.startsWith("/") === true ? state.from : "/healthcurve";
      void navigate(destination, { replace: true });
    } catch (caught: unknown) {
      setError(caught instanceof ApiError ? caught.message : "Sign-in is temporarily unavailable.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Box component="main" className="login-page">
      <Paper component="section" className="login-panel" aria-labelledby="login-heading" radius="xl" shadow="lg" withBorder>
        <Stack gap="lg">
          <Image className="login-logo" src={protectiveHorizonLogo} alt="HealthCurve.ai" fit="contain" />
          <Box>
            <Text className="eyebrow" mb="xs">Private health record</Text>
            <Title id="login-heading" order={1}>Sign in to HealthCurve.ai</Title>
            <Text className="brand-tagline" mt="sm"><strong>AI for AI:</strong> Artificial intelligence for adrenal insufficiency.</Text>
          </Box>
          <Paper className="login-safety-note" radius="md" p="md">
            <Text fw={650}>For organizing and reviewing your records—not diagnosis, emergency care, or medication advice.</Text>
            <Text size="sm" mt="xs">Your session stays on this device. Health details are never placed in the page title or URL.</Text>
          </Paper>
          {error === null ? null : <Alert color="red" role="alert" tabIndex={-1}>{error}</Alert>}
          <form onSubmit={(event) => { void submit(event); }}>
            <Stack gap="md">
              <TextInput
                id="email"
                name="email"
                type="email"
                label="Email"
                aria-label="Email"
                autoComplete="username"
                required
                size="md"
                value={email}
                onChange={(event) => { setEmail(event.currentTarget.value); }}
              />
              <PasswordInput
                id="password"
                name="password"
                label="Password"
                aria-label="Password"
                autoComplete="current-password"
                required
                size="md"
                value={password}
                onChange={(event) => { setPassword(event.currentTarget.value); }}
              />
              <Button type="submit" size="md" fullWidth loading={submitting}>
                {submitting ? "Signing in…" : "Sign in"}
              </Button>
            </Stack>
          </form>
        </Stack>
      </Paper>
    </Box>
  );
}

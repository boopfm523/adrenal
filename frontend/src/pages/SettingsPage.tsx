import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  confirmMfaEnrollment,
  deleteAccount,
  disconnectIntegration,
  downloadPrivateExport,
  getGarminDisconnectPreview,
  getGarminStatus,
  getMfaStatus,
  regenerateMfaRecoveryCodes,
  removeMfa,
  requestGarminSync,
  revokeAllSessions,
  startMfaEnrollment,
  type MfaEnrollment,
} from "../api/client";
import { sessionStore } from "../api/session";
import { useAuth } from "../auth/context";
import { Page } from "../components/Page";
import { ContextSettings } from "../components/ContextSettings";

function IntegrationControl({ provider, description }: { provider: "telegram" | "weather"; description: string }): React.JSX.Element {
  const mutation = useMutation({ mutationFn: ({ password, deleteData }: { password: string; deleteData: boolean }) => disconnectIntegration(provider, password, deleteData) });
  function submit(event: React.SyntheticEvent<HTMLFormElement>): void { event.preventDefault(); const data = new FormData(event.currentTarget); mutation.mutate({ password: data.get("password") as string, deleteData: data.get("delete_data") === "on" }); }
  const title = provider === "telegram" ? "Telegram logging" : "Open-Meteo weather";
  return <article className="settings-card"><h3>{title}</h3><p>{description}</p><form className="privacy-action" onSubmit={submit}><label>Current password<input name="password" type="password" required autoComplete="current-password" /></label><label className="checkbox-label"><input name="delete_data" type="checkbox" defaultChecked />Delete imported/provider-derived data as well as disconnecting</label><button type="submit" disabled={mutation.isPending}>Disconnect {provider}</button></form>{mutation.isSuccess ? <p className="success-message" role="status">Disconnected. {mutation.data.credentials_deleted} credential(s) and {mutation.data.data_rows_deleted} provider row(s) deleted.</p> : null}{mutation.isError ? <p className="error-summary" role="alert">The integration was not disconnected. Check your password.</p> : null}</article>;
}

function requestKey(): string {
  return `web-${Date.now().toString()}-${Math.random().toString(36).slice(2)}`;
}

function GarminControl(): React.JSX.Element {
  const queryClient = useQueryClient();
  const [deleteData, setDeleteData] = useState(true);
  const status = useQuery({ queryKey: ["garmin-status"], queryFn: getGarminStatus });
  const preview = useQuery({
    queryKey: ["garmin-disconnect-preview"],
    queryFn: getGarminDisconnectPreview,
  });
  const sync = useMutation({
    mutationFn: () => requestGarminSync(requestKey()),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["garmin-status"] });
    },
  });
  const disconnect = useMutation({
    mutationFn: ({ password, confirmation }: { password: string; confirmation: string }) =>
      disconnectIntegration("garmin", password, deleteData, confirmation),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["garmin-status"] }),
        queryClient.invalidateQueries({ queryKey: ["garmin-disconnect-preview"] }),
        queryClient.invalidateQueries({ queryKey: ["garmin-records"] }),
      ]);
    },
  });
  const confirmation = deleteData
    ? "DISCONNECT GARMIN AND DELETE DATA"
    : "DISCONNECT GARMIN";
  const capabilities = Object.entries(status.data?.capabilities ?? {});
  const warningCodes = status.data?.latest_sync_warning_codes ?? [];
  const canSync = status.data?.configured === true && status.data.state === "connected";

  return <article className="settings-card" id="garmin-connection">
    <h3>Garmin Connect</h3>
    <p>Read-only automatic sync uses the owner-selected unofficial client. First sign-in and any Garmin MFA happen only in the local setup command; credentials and tokens are never shown here. Reviewed FIT/CSV import remains available as a fallback.</p>
    {status.isPending ? <p role="status">Checking Garmin status…</p> : null}
    {status.isError ? <p className="error-summary" role="alert">Garmin status is unavailable.</p> : null}
    {status.data === undefined ? null : <dl className="integration-status">
      <div><dt>Configuration</dt><dd>{status.data.configured ? "Enabled" : "Disabled"}</dd></div>
      <div><dt>Connection</dt><dd>{status.data.state.replaceAll("_", " ")}</dd></div>
      <div><dt>Last successful sync</dt><dd>{status.data.last_success_at ?? "Not yet completed"}</dd></div>
      <div><dt>Last safe error code</dt><dd>{status.data.last_error_code ?? "None"}</dd></div>
    </dl>}
    {capabilities.length === 0 ? null : <details><summary>Latest metric availability</summary><ul>{capabilities.map(([name, value]) => <li key={name}>{name.replaceAll("_", " ")}: {value}</li>)}</ul></details>}
    {warningCodes.length === 0 ? null : <p className="privacy-note">Latest safe warning codes: {warningCodes.join(", ")}. Missing values remain unavailable, never zero.</p>}
    <button type="button" disabled={sync.isPending || !canSync} onClick={() => { sync.mutate(); }}>{sync.isPending ? "Queueing sync…" : "Sync Garmin now"}</button>
    {sync.isSuccess ? <p className="success-message" role="status">Garmin sync queued. Refresh status after the worker finishes.</p> : null}
    {sync.isError ? <p className="error-summary" role="alert">The Garmin sync was not queued. Review the connection status.</p> : null}
    <form className="privacy-action danger-zone" onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); disconnect.mutate({ password: data.get("password") as string, confirmation: data.get("confirmation") as string }); }}>
      <h4>Disconnect Garmin</h4>
      <p>{preview.data === undefined ? "Loading impact preview…" : `${preview.data.automatic_fact_rows.toString()} automatic fact row(s), ${preview.data.reviewed_import_fact_rows.toString()} reviewed import fact row(s), and ${preview.data.sync_run_rows.toString()} sync provenance row(s) are currently recorded.`}</p>
      <label className="checkbox-label"><input name="delete_data" type="checkbox" checked={deleteData} onChange={(event) => { setDeleteData(event.target.checked); }} />Delete Garmin-derived facts and sync provenance as well as disconnecting</label>
      <label>Current password<input name="password" type="password" required autoComplete="current-password" /></label>
      <label>Type {confirmation}<input name="confirmation" required autoComplete="off" /></label>
      <button type="submit" disabled={disconnect.isPending}>Disconnect Garmin</button>
    </form>
    {disconnect.isSuccess ? <p className="success-message" role="status">Disconnect accepted. {disconnect.data.data_rows_deleted} provider row(s) deleted. Local Garmin token cleanup is {disconnect.data.disconnect_requested ? "queued" : "already complete"}.</p> : null}
    {disconnect.isError ? <p className="error-summary" role="alert">Garmin was not disconnected. Check the password and exact confirmation phrase.</p> : null}
  </article>;
}

function MfaSettings(): React.JSX.Element {
  const status = useQuery({ queryKey: ["mfa-status"], queryFn: getMfaStatus });
  const [enrollment, setEnrollment] = useState<MfaEnrollment | null>(null);
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);
  const start = useMutation({ mutationFn: startMfaEnrollment, onSuccess: (value) => { setEnrollment(value); setRecoveryCodes(null); } });
  const confirm = useMutation({ mutationFn: confirmMfaEnrollment, onSuccess: (value) => { setEnrollment(null); setRecoveryCodes(value.recovery_codes); void status.refetch(); } });
  const regenerate = useMutation({ mutationFn: ({ password, code }: { password: string; code: string }) => regenerateMfaRecoveryCodes(password, code), onSuccess: (value) => { setRecoveryCodes(value.recovery_codes); void status.refetch(); } });
  const removal = useMutation({ mutationFn: ({ password, code }: { password: string; code: string }) => removeMfa(password, code), onSuccess: () => { sessionStore.clear(); } });

  const error = start.isError || confirm.isError || regenerate.isError || removal.isError;
  return <div className="settings-card" id="mfa-settings">
    <h3>Multi-factor authentication</h3>
    <p>Use a standard authenticator app. The seed is encrypted with the external credential key; HealthCurve never sends it to an AI service.</p>
    {status.isLoading ? <p role="status">Checking MFA status…</p> : null}
    {status.data?.enabled === true ? <>
      <p className="success-message"><strong>Enabled.</strong> {status.data.recovery_codes_remaining} unused recovery code(s) remain.</p>
      <form className="privacy-action" onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); regenerate.mutate({ password: data.get("password") as string, code: data.get("code") as string }); }}>
        <h4>Replace recovery codes</h4><p>This invalidates every previous recovery code. The replacements are shown once.</p>
        <label>Current password<input name="password" type="password" required autoComplete="current-password" /></label>
        <label>Authenticator or recovery code<input name="code" required autoComplete="one-time-code" /></label>
        <button type="submit" disabled={regenerate.isPending}>Generate replacements</button>
      </form>
      <form className="privacy-action danger-zone" onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); removal.mutate({ password: data.get("password") as string, code: data.get("code") as string }); }}>
        <h4>Remove MFA</h4><p>This signs out every device. Production will refuse password-only login until MFA is enrolled locally again.</p>
        <label>Current password<input name="password" type="password" required autoComplete="current-password" /></label>
        <label>Authenticator or recovery code<input name="code" required autoComplete="one-time-code" /></label>
        <button type="submit" disabled={removal.isPending}>Remove MFA and sign out</button>
      </form>
    </> : enrollment === null ? <form className="privacy-action" onSubmit={(event) => { event.preventDefault(); start.mutate(new FormData(event.currentTarget).get("password") as string); }}>
      <p>MFA is not enabled. Re-enter your password to begin enrollment.</p>
      <label>Current password<input name="password" type="password" required autoComplete="current-password" /></label>
      <button type="submit" disabled={start.isPending}>Start enrollment</button>
    </form> : <div className="privacy-action">
      <p><strong>Add the account before continuing.</strong> Enter this secret manually in your authenticator. It is shown only during this enrollment.</p>
      <code className="credential-value">{enrollment.secret}</code>
      <form onSubmit={(event) => { event.preventDefault(); confirm.mutate(new FormData(event.currentTarget).get("code") as string); }}>
        <label>Current 6-digit code<input name="code" required inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" /></label>
        <button type="submit" disabled={confirm.isPending}>Verify and enable MFA</button>
      </form>
    </div>}
    {recoveryCodes === null ? null : <div className="recovery-codes" role="status"><h4>Save these recovery codes now</h4><p>Each works once. They will not be shown again after you leave or replace them. Store them off-device in a protected password manager or sealed copy.</p><ul>{recoveryCodes.map((code) => <li key={code}><code>{code}</code></li>)}</ul></div>}
    {error ? <p className="error-summary" role="alert">The MFA change was not completed. Check the password/code and try again.</p> : null}
  </div>;
}

export function SettingsPage(): React.JSX.Element {
  const { session } = useAuth();
  const [includeAi, setIncludeAi] = useState(false);
  const exportMutation = useMutation({ mutationFn: (password: string) => downloadPrivateExport(password, includeAi), onSuccess: (blob) => { const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = "healthcurve-export.json"; link.click(); URL.revokeObjectURL(url); } });
  const sessions = useMutation({ mutationFn: revokeAllSessions, onSuccess: () => { sessionStore.clear(); } });
  const account = useMutation({ mutationFn: ({ password, confirmation }: { password: string; confirmation: string }) => deleteAccount(password, confirmation), onSuccess: () => { sessionStore.clear(); } });

  return <Page title="Settings & privacy" description="Control access, integrations, exports, retention, and deletion without exposing stored secrets.">
    <section aria-labelledby="security-heading"><h2 id="security-heading">Account and sessions</h2><div className="settings-card"><p><strong>Signed in as:</strong> {session?.user.email}</p><p><strong>Default timezone:</strong> {session?.user.defaultTimezone}</p><button type="button" onClick={() => { sessions.mutate(); }} disabled={sessions.isPending}>Sign out every session</button>{sessions.isError ? <p className="error-summary" role="alert">Sessions could not be revoked.</p> : null}</div><MfaSettings /></section>
    <section aria-labelledby="integration-heading"><h2 id="integration-heading">Integrations</h2><p>Stored tokens are encrypted and are never displayed here.</p><div className="settings-grid"><GarminControl /><IntegrationControl provider="telegram" description="HealthCurve retains Telegram update IDs and outcomes for replay protection. Pending health drafts may retain message text; confirmed or cancelled drafts purge that raw text. A /beads-add directive is sent only to the configured local model; its outbox and Bead retain the validated proposal and a one-way message hash, not the raw directive. Telegram itself may retain messages under your Telegram account settings." /><IntegrationControl provider="weather" description="A confirmed Telegram location sends only rounded 0.1-degree coordinates and fixed current-weather field names to Open-Meteo. No health text, account identifier, event time, or token is sent. Open-Meteo returns the observation time and may retain request logs under its policy. Deleting weather data leaves your coarse location and health facts intact." /></div></section>
    <ContextSettings />
    <section aria-labelledby="export-heading"><h2 id="export-heading">Export</h2><form className="settings-card privacy-action" onSubmit={(event) => { event.preventDefault(); exportMutation.mutate(new FormData(event.currentTarget).get("password") as string); }}><p>Download facts and physician-approved plan data. Integration credentials are never included.</p><label>Current password<input name="password" type="password" required autoComplete="current-password" /></label><label className="checkbox-label"><input type="checkbox" checked={includeAi} onChange={(event) => { setIncludeAi(event.target.checked); }} />Include separately labeled AI analysis</label><button type="submit" disabled={exportMutation.isPending}>Download private export</button>{exportMutation.isError ? <p className="error-summary" role="alert">The export was not created. Check your password.</p> : null}</form></section>
    <section aria-labelledby="retention-heading"><h2 id="retention-heading">Backups and audit</h2><div className="settings-card"><p><strong>Backup status:</strong> Not available to the web application. Use the private backup status command and restore drill.</p><p><strong>Deletion and backups:</strong> Deletion removes live application data. Encrypted backups can retain deleted data until those backup copies reach their configured expiry.</p><p><strong>Audit retention:</strong> Structural audit entries recording deletion survive account and record deletion. They contain action metadata, not the deleted health content.</p></div></section>
    <section aria-labelledby="delete-heading"><h2 id="delete-heading">Delete account</h2><form className="settings-card privacy-action danger-zone" onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); account.mutate({ password: data.get("password") as string, confirmation: data.get("confirmation") as string }); }}><p>This permanently deletes live health facts, plans, AI drafts, retained lab files, integration data, credentials, and sessions. It cannot be undone.</p><label>Current password<input name="password" type="password" required autoComplete="current-password" /></label><label>Type DELETE MY HEALTHCURVE ACCOUNT<input name="confirmation" required autoComplete="off" /></label><button type="submit" disabled={account.isPending}>Permanently delete account</button>{account.isError ? <p className="error-summary" role="alert">The account was not deleted. Check the password and confirmation phrase.</p> : null}</form></section>
  </Page>;
}

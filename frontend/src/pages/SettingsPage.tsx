import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import {
  deleteAccount,
  disconnectIntegration,
  getPrivateExports,
  getGarminDisconnectPreview,
  getGarminStatus,
  requestGarminSync,
  requestPrivateExport,
  revokeAllSessions,
  type GarminSyncRequest,
} from "../api/client";
import { sessionStore } from "../api/session";
import { useAuth } from "../auth/context";
import { Page } from "../components/Page";
import { ContextSettings } from "../components/ContextSettings";
import { formatDecimal } from "../format";

function IntegrationControl({ provider, description }: { provider: "telegram" | "weather"; description: string }): React.JSX.Element {
  const mutation = useMutation({ mutationFn: ({ password, deleteData }: { password: string; deleteData: boolean }) => disconnectIntegration(provider, password, deleteData) });
  function submit(event: React.SyntheticEvent<HTMLFormElement>): void { event.preventDefault(); const data = new FormData(event.currentTarget); mutation.mutate({ password: data.get("password") as string, deleteData: data.get("delete_data") === "on" }); }
  const title = provider === "telegram" ? "Telegram logging" : "Open-Meteo weather";
  return <article className="settings-card"><h3>{title}</h3><p>{description}</p><form className="privacy-action" onSubmit={submit}><label>Current password<input name="password" type="password" required autoComplete="current-password" /></label><label className="checkbox-label"><input name="delete_data" type="checkbox" defaultChecked />Delete imported/provider-derived data as well as disconnecting</label><button type="submit" disabled={mutation.isPending}>Disconnect {provider}</button></form>{mutation.isSuccess ? <p className="success-message" role="status">Disconnected. {formatDecimal(mutation.data.credentials_deleted)} credential(s) and {formatDecimal(mutation.data.data_rows_deleted)} provider row(s) deleted.</p> : null}{mutation.isError ? <p className="error-summary" role="alert">The integration was not disconnected. Check your password.</p> : null}</article>;
}

function requestKey(): string {
  return `web-${Date.now().toString()}-${Math.random().toString(36).slice(2)}`;
}

function syncResultMessage(result: GarminSyncRequest): string {
  const window = `${result.requested_start_date} through ${result.requested_end_date}`;
  if (result.disposition === "coalesced_active") {
    return `An equivalent Garmin sync for ${window} is already queued or running. No duplicate provider read was added.`;
  }
  if (result.disposition === "cooldown_reused") {
    const until = result.cooldown_until === null
      ? "the cooldown ends"
      : result.cooldown_until.replace("T", " ").replace("Z", " UTC");
    return `Garmin already completed ${window} recently. No duplicate provider read was added; the cooldown lasts until ${until}. Use Refresh recent Garmin window only if you deliberately need another read now.`;
  }
  if (result.disposition === "idempotent_replay") {
    return `This exact request for ${window} was already accepted. No duplicate provider read was added.`;
  }
  return result.disposition === "refresh_queued"
    ? `A deliberate refresh of ${window} was queued.`
    : `Garmin sync for ${window} was queued.`;
}

function garminOriginLabel(origin: string | null | undefined): string {
  if (origin === "scheduled") return "Scheduled automatic sync";
  if (origin === "manual") return "Manual sync";
  if (origin === "manual_refresh") return "Manual refresh";
  if (origin === "legacy") return "Origin unavailable (older sync)";
  return "Not yet completed";
}

const GARMIN_STATUS_POLL_MS = 2_000;
const GARMIN_STATUS_POLL_LIMIT_MS = 120_000;

interface GarminStatusPolling {
  baselineLastSuccess: string | null;
  expiresAt: number;
  sawActiveStatus: boolean;
}

function GarminControl(): React.JSX.Element {
  const queryClient = useQueryClient();
  const [deleteData, setDeleteData] = useState(true);
  const [isStatusPolling, setIsStatusPolling] = useState(false);
  const statusPolling = useRef<GarminStatusPolling | null>(null);
  const status = useQuery({
    queryKey: ["garmin-status"],
    queryFn: getGarminStatus,
    refetchInterval: (query) => {
      const tracker = statusPolling.current;
      if (tracker === null) return false;
      if (Date.now() >= tracker.expiresAt) {
        statusPolling.current = null;
        queueMicrotask(() => { setIsStatusPolling(false); });
        return false;
      }
      const current = query.state.data;
      if (current === undefined) return GARMIN_STATUS_POLL_MS;
      const latest = current.latest_sync_status;
      if (latest === "queued" || latest === "running") tracker.sawActiveStatus = true;
      const terminal = latest === "completed" || latest === "completed_with_warnings" || latest === "failed";
      const successChanged = current.last_success_at !== tracker.baselineLastSuccess;
      if (terminal && (tracker.sawActiveStatus || successChanged)) {
        statusPolling.current = null;
        queueMicrotask(() => { setIsStatusPolling(false); });
        void Promise.all([
          queryClient.invalidateQueries({ queryKey: ["garmin-records"] }),
          queryClient.invalidateQueries({ queryKey: ["garmin-samples"] }),
          queryClient.invalidateQueries({ queryKey: ["garmin-sleep"] }),
          queryClient.invalidateQueries({ queryKey: ["healthcurve"] }),
        ]);
        return false;
      }
      return GARMIN_STATUS_POLL_MS;
    },
  });
  const preview = useQuery({
    queryKey: ["garmin-disconnect-preview"],
    queryFn: getGarminDisconnectPreview,
  });
  const sync = useMutation({
    mutationFn: (refresh: boolean) => requestGarminSync(requestKey(), refresh),
    onSuccess: async (result) => {
      if (["queued", "refresh_queued", "coalesced_active"].includes(result.disposition)) {
        statusPolling.current = {
          baselineLastSuccess: status.data?.last_success_at ?? null,
          expiresAt: Date.now() + GARMIN_STATUS_POLL_LIMIT_MS,
          sawActiveStatus: false,
        };
        setIsStatusPolling(true);
      } else {
        statusPolling.current = null;
        setIsStatusPolling(false);
      }
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
      <div><dt>Latest sync origin</dt><dd>{garminOriginLabel(status.data.latest_sync_origin)}</dd></div>
      <div><dt>Last safe error code</dt><dd>{status.data.last_error_code ?? "None"}</dd></div>
    </dl>}
    {capabilities.length === 0 ? null : <details><summary>Latest metric availability</summary><ul>{capabilities.map(([name, value]) => <li key={name}>{name.replaceAll("_", " ")}: {value}</li>)}</ul></details>}
    {warningCodes.length === 0 ? null : <p className="privacy-note">Latest safe warning codes: {warningCodes.join(", ")}. Missing values remain unavailable, never zero.</p>}
    <p>Automatic sync runs once per local day. Equivalent queued or running windows are shared, and a recently completed window has a 30-minute cooldown. The refresh control deliberately bypasses only that completed-window cooldown.</p>
    <div className="button-row">
      <button type="button" disabled={sync.isPending || !canSync} onClick={() => { sync.mutate(false); }}>{sync.isPending ? "Queueing sync…" : "Sync Garmin now"}</button>
      <button type="button" disabled={sync.isPending || !canSync} onClick={() => { sync.mutate(true); }}>Refresh recent Garmin window</button>
    </div>
    {sync.isSuccess ? <p className="success-message" role="status">{syncResultMessage(sync.data)}</p> : null}
    {isStatusPolling ? <p className="privacy-note" role="status">Garmin sync is queued or running. This status will update automatically.</p> : null}
    {sync.isError ? <p className="error-summary" role="alert">The Garmin sync was not queued. Review the connection status.</p> : null}
    <form className="privacy-action danger-zone" onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); disconnect.mutate({ password: data.get("password") as string, confirmation: data.get("confirmation") as string }); }}>
      <h4>Disconnect Garmin</h4>
      <p>{preview.data === undefined ? "Loading impact preview…" : `${formatDecimal(preview.data.automatic_fact_rows)} automatic fact row(s), ${formatDecimal(preview.data.reviewed_import_fact_rows)} reviewed import fact row(s), and ${formatDecimal(preview.data.sync_run_rows)} sync provenance row(s) are currently recorded.`}</p>
      <label className="checkbox-label"><input name="delete_data" type="checkbox" checked={deleteData} onChange={(event) => { setDeleteData(event.target.checked); }} />Delete Garmin-derived facts and sync provenance as well as disconnecting</label>
      <label>Current password<input name="password" type="password" required autoComplete="current-password" /></label>
      <label>Type {confirmation}<input name="confirmation" required autoComplete="off" /></label>
      <button type="submit" disabled={disconnect.isPending}>Disconnect Garmin</button>
    </form>
    {disconnect.isSuccess ? <p className="success-message" role="status">Disconnect accepted. {formatDecimal(disconnect.data.data_rows_deleted)} provider row(s) deleted. Local Garmin token cleanup is {disconnect.data.disconnect_requested ? "queued" : "already complete"}.</p> : null}
    {disconnect.isError ? <p className="error-summary" role="alert">Garmin was not disconnected. Check the password and exact confirmation phrase.</p> : null}
  </article>;
}

export function SettingsPage(): React.JSX.Element {
  const { session } = useAuth();
  const [includeAi, setIncludeAi] = useState(false);
  const [includeSensitive, setIncludeSensitive] = useState(true);
  const queryClient = useQueryClient();
  const privateExports = useQuery({
    queryKey: ["private-exports"],
    queryFn: getPrivateExports,
    refetchInterval: (query) => query.state.data?.items.some((item) => item.status === "queued" || item.status === "running") === true ? 2_000 : false,
  });
  const exportMutation = useMutation({
    mutationFn: (password: string) => requestPrivateExport(password, includeAi, includeSensitive, requestKey()),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["private-exports"] }); },
  });
  const sessions = useMutation({ mutationFn: revokeAllSessions, onSuccess: () => { sessionStore.clear(); } });
  const account = useMutation({ mutationFn: ({ password, confirmation }: { password: string; confirmation: string }) => deleteAccount(password, confirmation), onSuccess: () => { sessionStore.clear(); } });

  return <Page title="Settings & privacy" description="Control access, integrations, exports, retention, and deletion without exposing stored secrets.">
    <section aria-labelledby="security-heading"><h2 id="security-heading">Account and sessions</h2><div className="settings-card"><p><strong>Signed in as:</strong> {session?.user.email}</p><p><strong>Default timezone:</strong> {session?.user.defaultTimezone}</p><p>HealthCurve uses your password and is reachable only through the approved Tailscale network. Sessions remain protected by secure cookies, CSRF checks, expiration, revocation, and login rate limits.</p><button type="button" onClick={() => { sessions.mutate(); }} disabled={sessions.isPending}>Sign out every session</button>{sessions.isError ? <p className="error-summary" role="alert">Sessions could not be revoked.</p> : null}</div></section>
    <section aria-labelledby="integration-heading"><h2 id="integration-heading">Integrations</h2><p>Stored tokens are encrypted and are never displayed here.</p><div className="settings-grid"><GarminControl /><IntegrationControl provider="telegram" description="HealthCurve retains Telegram update IDs and outcomes for replay protection. Pending health drafts may retain message text; confirmed or cancelled drafts purge that raw text. A /beads-add directive is sent only to the configured local model; its outbox and Bead retain the validated proposal and a one-way message hash, not the raw directive. Telegram itself may retain messages under your Telegram account settings." /><IntegrationControl provider="weather" description="A confirmed Telegram location sends only rounded 0.1-degree coordinates and fixed current-weather field names to Open-Meteo. No health text, account identifier, event time, or token is sent. Open-Meteo returns the observation time and may retain request logs under its policy. Deleting weather data leaves your coarse location and health facts intact." /></div></section>
    <ContextSettings />
    <section aria-labelledby="export-heading"><h2 id="export-heading">Export</h2><form className="settings-card privacy-action" onSubmit={(event) => { event.preventDefault(); exportMutation.mutate(new FormData(event.currentTarget).get("password") as string); }}><p>Create a complete private JSON export. Large histories run safely in the background; this page keeps the durable progress and download available after a refresh. Integration credentials are never included.</p><label>Current password<input name="password" type="password" required autoComplete="current-password" /></label><label className="checkbox-label"><input type="checkbox" checked={includeSensitive} onChange={(event) => { setIncludeSensitive(event.target.checked); }} />Include sensitive diary and life-event text</label><label className="checkbox-label"><input type="checkbox" checked={includeAi} onChange={(event) => { setIncludeAi(event.target.checked); }} />Include separately labeled AI analysis</label><button type="submit" disabled={exportMutation.isPending}>{exportMutation.isPending ? "Queueing export…" : "Create private export"}</button>{exportMutation.isSuccess ? <p className="success-message" role="status">Private export queued. Progress appears below.</p> : null}{exportMutation.isError ? <p className="error-summary" role="alert">The export was not queued. Check your password and try again.</p> : null}</form>
    {privateExports.isPending ? <p role="status">Loading private export history…</p> : null}
    {privateExports.isError ? <p className="error-summary" role="alert">Private export history is unavailable.</p> : null}
    {privateExports.data?.items.length === 0 ? <p>No private exports have been requested.</p> : null}
    {privateExports.data === undefined || privateExports.data.items.length === 0 ? null : <div className="settings-grid" aria-live="polite">{privateExports.data.items.map((item) => <article className="settings-card" key={item.id}><h3>Private export</h3><p><strong>Status:</strong> {item.status.replaceAll("_", " ")}</p><p><strong>Requested:</strong> <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString()}</time></p>{item.total_rows === null ? <p>Preparing row count…</p> : <><p><strong>Progress:</strong> {formatDecimal(item.processed_rows)} of {formatDecimal(item.total_rows)} rows ({formatDecimal(item.progress_percent ?? 0)}%)</p><progress aria-label="Private export progress" max={item.total_rows === 0 ? 1 : item.total_rows} value={item.total_rows === 0 ? 1 : item.processed_rows} /></>}<p><strong>Attempt:</strong> {formatDecimal(item.attempt_count)} of {formatDecimal(item.max_attempts)}</p>{item.last_error_code === null ? null : <p className="error-summary">Safe error code: {item.last_error_code}</p>}{item.status === "queued" && item.attempt_count > 0 && item.next_attempt_at !== null ? <p>Automatic retry scheduled for <time dateTime={item.next_attempt_at}>{new Date(item.next_attempt_at).toLocaleString()}</time>.</p> : null}{item.download_url === null ? null : <><p>Available until <time dateTime={item.expires_at}>{new Date(item.expires_at).toLocaleString()}</time>.</p><a className="button-link" href={item.download_url} download>Download completed export</a></>}</article>)}</div>}
    </section>
    <section aria-labelledby="retention-heading"><h2 id="retention-heading">Backups and audit</h2><div className="settings-card"><p><strong>Backup status:</strong> Not available to the web application. Use the private backup status command and restore drill.</p><p><strong>Deletion and backups:</strong> Deletion removes live application data. Encrypted backups can retain deleted data until those backup copies reach their configured expiry.</p><p><strong>Audit retention:</strong> Structural audit entries recording deletion survive account and record deletion. They contain action metadata, not the deleted health content.</p></div></section>
    <section aria-labelledby="delete-heading"><h2 id="delete-heading">Delete account</h2><form className="settings-card privacy-action danger-zone" onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); account.mutate({ password: data.get("password") as string, confirmation: data.get("confirmation") as string }); }}><p>This permanently deletes live health facts, plans, AI drafts, retained lab files, integration data, credentials, and sessions. It cannot be undone.</p><label>Current password<input name="password" type="password" required autoComplete="current-password" /></label><label>Type DELETE MY HEALTHCURVE ACCOUNT<input name="confirmation" required autoComplete="off" /></label><button type="submit" disabled={account.isPending}>Permanently delete account</button>{account.isError ? <p className="error-summary" role="alert">The account was not deleted. Check the password and confirmation phrase.</p> : null}</form></section>
  </Page>;
}

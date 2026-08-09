import type { components } from "./schema";
import { sessionStore, type ActiveSession } from "./session";

type LoginRequest = components["schemas"]["LoginRequest"];
type LoginResponse = components["schemas"]["LoginResponse"];
type WhoAmI = components["schemas"]["WhoAmI"];
export type PlanComparisonDay = components["schemas"]["PlanComparisonDay"];
export type Episode = components["schemas"]["EpisodeOut"];
export type EpisodeInput = components["schemas"]["EpisodeIn"];
export type EpisodeUpdate = components["schemas"]["EpisodeUpdate"];
export type Dose = components["schemas"]["DoseOut"];
export type DoseInput = components["schemas"]["DoseIn"];
export type DoseCorrectionInput = components["schemas"]["DoseCorrectionIn"];
export type Timeline = components["schemas"]["TimelinePage"];
export type Symptom = components["schemas"]["SymptomOut"];
export type SymptomCorrectionInput = components["schemas"]["SymptomCorrectionIn"];
export type DiaryEntry = components["schemas"]["DiaryOut"];
export type LifeEvent = components["schemas"]["LifeEventOut"];
export type RegimenVersion = components["schemas"]["RegimenVersionOut"];
export type AnalyticsSummary = components["schemas"]["AnalyticsSummaryOut"];
export type DataQuality = components["schemas"]["DataQualityOut"];
export type ReportSummary = components["schemas"]["ReportOut"];
export type ReportPreview = components["schemas"]["ReportPreviewOut"];
export type ReportCreate = components["schemas"]["ReportCreateRequest"];
export type MfaStatus = components["schemas"]["MfaStatus"];
export type MfaEnrollment = components["schemas"]["MfaEnrollmentOut"];
export type MfaRecoveryCodes = components["schemas"]["MfaRecoveryCodesOut"];
export type ContextEvent = components["schemas"]["ContextOut"];
export type ContextInput = components["schemas"]["ContextIn"];

interface ApiErrorBody {
  detail?: string;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function isWriteMethod(method: string): boolean {
  return !["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase());
}

function toSession(response: LoginResponse | WhoAmI): ActiveSession {
  return {
    csrfToken: response.csrf_token,
    user: {
      email: response.email,
      displayName: response.display_name,
      defaultTimezone: response.default_timezone,
      mfaEnabled: response.mfa_enabled,
    },
  };
}

async function parseError(response: Response): Promise<ApiError> {
  let detail = "The request could not be completed.";
  try {
    const body = (await response.json()) as ApiErrorBody;
    if (typeof body.detail === "string") detail = body.detail;
  } catch {
    // Privacy-safe fallback: never surface server response bodies as HTML or diagnostics.
  }
  return new ApiError(response.status, detail);
}

export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = init.method ?? "GET";
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body !== undefined && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (isWriteMethod(method)) {
    const csrfToken = sessionStore.get()?.csrfToken;
    if (csrfToken !== undefined) headers.set("X-CSRF-Token", csrfToken);
  }

  const response = await fetch(`/api/v1${path}`, {
    ...init,
    method,
    headers,
    credentials: "include",
  });

  if (response.status === 401) sessionStore.clear();
  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function login(payload: LoginRequest): Promise<ActiveSession> {
  const response = await apiRequest<LoginResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  const session = toSession(response);
  sessionStore.set(session);
  return session;
}

export async function restoreSession(): Promise<ActiveSession> {
  const response = await apiRequest<WhoAmI>("/auth/me");
  const session = toSession(response);
  sessionStore.set(session);
  return session;
}

export async function logout(): Promise<void> {
  try {
    await apiRequest<unknown>("/auth/logout", { method: "POST" });
  } finally {
    sessionStore.clear();
  }
}

export function getMfaStatus(): Promise<MfaStatus> {
  return apiRequest<MfaStatus>("/auth/mfa");
}

export function startMfaEnrollment(password: string): Promise<MfaEnrollment> {
  return apiRequest<MfaEnrollment>("/auth/mfa/enrollment", {
    method: "POST",
    body: JSON.stringify({ password }),
  });
}

export function confirmMfaEnrollment(code: string): Promise<MfaRecoveryCodes> {
  return apiRequest<MfaRecoveryCodes>("/auth/mfa/enrollment/confirm", {
    method: "POST",
    body: JSON.stringify({ code }),
  });
}

export function regenerateMfaRecoveryCodes(
  password: string,
  code: string,
): Promise<MfaRecoveryCodes> {
  return apiRequest<MfaRecoveryCodes>("/auth/mfa/recovery-codes", {
    method: "POST",
    body: JSON.stringify({ password, code }),
  });
}

export async function removeMfa(password: string, code: string): Promise<void> {
  await apiRequest<unknown>("/auth/mfa", {
    method: "DELETE",
    body: JSON.stringify({ password, code }),
  });
}

export function getPlanComparison(day: string, timezone: string): Promise<PlanComparisonDay> {
  const params = new URLSearchParams({ day, timezone });
  return apiRequest<PlanComparisonDay>(`/doses/plan-comparison?${params.toString()}`);
}

export function getOpenEpisodes(): Promise<Episode[]> {
  return apiRequest<Episode[]>("/stress-episodes?open_only=true");
}

export function getEpisodes(): Promise<Episode[]> {
  return apiRequest<Episode[]>("/stress-episodes");
}

export function createEpisode(payload: EpisodeInput): Promise<Episode> {
  return apiRequest<Episode>("/stress-episodes", { method: "POST", body: JSON.stringify(payload) });
}

export function updateEpisode(id: string, payload: EpisodeUpdate): Promise<Episode> {
  return apiRequest<Episode>(`/stress-episodes/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
}

export function recordDose(payload: DoseInput): Promise<Dose> {
  return apiRequest<Dose>("/doses", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getDoses(includeSuperseded = false): Promise<Dose[]> {
  const params = new URLSearchParams();
  if (includeSuperseded) params.set("include_superseded", "true");
  const query = params.size === 0 ? "" : `?${params.toString()}`;
  return apiRequest<Dose[]>(`/doses${query}`);
}

export function correctDose(doseId: string, payload: DoseCorrectionInput): Promise<Dose> {
  return apiRequest<Dose>(`/doses/${doseId}/correct`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export interface TimelineFilters {
  type: string;
  dateFrom: string;
  dateTo: string;
  timezone: string;
  includeSensitive: boolean;
}

export function getTimeline(filters: TimelineFilters): Promise<Timeline> {
  const params = new URLSearchParams({ timezone: filters.timezone });
  if (filters.type !== "") params.set("types", filters.type);
  if (filters.dateFrom !== "") params.set("local_date_from", filters.dateFrom);
  if (filters.dateTo !== "") params.set("local_date_to", filters.dateTo);
  if (filters.includeSensitive) params.set("include_sensitive", "true");
  return apiRequest<Timeline>(`/timeline?${params.toString()}`);
}

export function getContextEvents(): Promise<ContextEvent[]> {
  return apiRequest<ContextEvent[]>("/context-events");
}

export function createContextEvent(payload: ContextInput): Promise<ContextEvent> {
  return apiRequest<ContextEvent>("/context-events", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function deleteContextEvent(id: string, password: string): Promise<void> {
  await apiRequest<unknown>(`/context-events/${id}`, {
    method: "DELETE",
    body: JSON.stringify({ password }),
  });
}

export function getSymptoms(includeSuperseded = false): Promise<Symptom[]> {
  return apiRequest<Symptom[]>(`/symptoms${includeSuperseded ? "?include_superseded=true" : ""}`);
}

export function correctSymptom(id: string, payload: SymptomCorrectionInput): Promise<Symptom> {
  return apiRequest<Symptom>(`/symptoms/${id}/correct`, { method: "POST", body: JSON.stringify(payload) });
}

export function getDiaryEntries(includeSensitive = false): Promise<DiaryEntry[]> {
  return apiRequest<DiaryEntry[]>(`/diary-events${includeSensitive ? "?include_sensitive=true" : ""}`);
}

export function getLifeEvents(includeSensitive = false): Promise<LifeEvent[]> {
  return apiRequest<LifeEvent[]>(`/life-events${includeSensitive ? "?include_sensitive=true" : ""}`);
}

export function getRegimens(): Promise<RegimenVersion[]> {
  return apiRequest<RegimenVersion[]>("/regimens");
}

export function getActiveRegimen(): Promise<RegimenVersion | null> {
  return apiRequest<RegimenVersion | null>("/regimens/active");
}

export function getRegimenDiff(olderId: string, newerId: string): Promise<Record<string, string[]>> {
  return apiRequest<Record<string, string[]>>(`/regimens/${olderId}/diff/${newerId}`);
}

export function getAnalyticsSummary(dateFrom: string, dateTo: string, timezone: string): Promise<AnalyticsSummary> {
  const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo, timezone });
  return apiRequest<AnalyticsSummary>(`/analytics/summary?${params.toString()}`);
}

export function getDataQuality(): Promise<DataQuality> {
  return apiRequest<DataQuality>("/data-quality");
}

export function getReports(): Promise<ReportSummary[]> {
  return apiRequest<ReportSummary[]>("/reports");
}

export function getReport(id: string): Promise<ReportPreview> {
  return apiRequest<ReportPreview>(`/reports/${id}`);
}

export function createReport(payload: ReportCreate): Promise<ReportSummary> {
  return apiRequest<ReportSummary>("/reports", { method: "POST", body: JSON.stringify(payload) });
}

export interface IntegrationDeletionResult { credentials_deleted: number; data_rows_deleted: number; }

export function disconnectIntegration(provider: "garmin" | "telegram", password: string, deleteData: boolean): Promise<IntegrationDeletionResult> {
  return apiRequest<IntegrationDeletionResult>(`/privacy/integrations/${provider}`, { method: "DELETE", body: JSON.stringify({ password, delete_data: deleteData }) });
}

export async function revokeAllSessions(): Promise<void> {
  await apiRequest<unknown>("/auth/logout-everywhere", { method: "POST" });
}

export async function deleteAccount(password: string, confirmation: string): Promise<void> {
  await apiRequest<unknown>("/privacy/account", { method: "DELETE", body: JSON.stringify({ password, confirmation }) });
}

export async function downloadPrivateExport(password: string, includeAi: boolean): Promise<Blob> {
  const headers = new Headers({ "Content-Type": "application/json", Accept: "application/json" });
  const csrfToken = sessionStore.get()?.csrfToken;
  if (csrfToken !== undefined) headers.set("X-CSRF-Token", csrfToken);
  const response = await fetch("/api/v1/privacy/export", { method: "POST", headers, credentials: "include", body: JSON.stringify({ password, include_ai: includeAi, include_sensitive: true }) });
  if (!response.ok) throw await parseError(response);
  return response.blob();
}

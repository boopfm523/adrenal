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
export type RegimenInput = components["schemas"]["RegimenVersionIn"];
export type RegimenApprovalInput = components["schemas"]["RegimenApprovalIn"];
export type Medication = components["schemas"]["MedicationOut"];
export type MedicationInput = components["schemas"]["MedicationIn"];
export type AnalyticsSummary = components["schemas"]["AnalyticsSummaryOut"];
export type DataQuality = components["schemas"]["DataQualityOut"];
export type ReportSummary = components["schemas"]["ReportOut"];
export type ReportPreview = components["schemas"]["ReportPreviewOut"];
export type ReportCreate = components["schemas"]["ReportCreateRequest"];
export type ContextEvent = components["schemas"]["ContextOut"];
export type ContextInput = components["schemas"]["ContextIn"];
export type BloodPressure = components["schemas"]["BloodPressureOut"];
export type BloodPressureInput = components["schemas"]["BloodPressureIn"];
export type BloodPressureCorrectionInput = components["schemas"]["BloodPressureCorrectionIn"];
export type Weight = components["schemas"]["WeightOut"];
export type WeightInput = components["schemas"]["WeightIn"];
export type WeightCorrectionInput = components["schemas"]["WeightCorrectionIn"];
export type LabResult = components["schemas"]["LabResultOut"];
export type GarminStatus = components["schemas"]["GarminStatusOut"];
export type GarminRecord = components["schemas"]["GarminRecordOut"];
export type GarminRecords = components["schemas"]["GarminRecordsOut"];
export type GarminDisconnectPreview = components["schemas"]["GarminDisconnectPreviewOut"];

export interface LabDocument {
  document_id: string;
  display_name: string;
  media_type: string;
  sha256: string;
  byte_size: number;
  status: "pending" | "stored" | "rejected" | "deleted";
  page_count: number | null;
  rejection_reason: string | null;
  created_at: string;
  validated_at: string | null;
  extraction_status: "pending" | "draft_ready";
  extraction_draft_id: string | null;
  draft_state?: "pending" | "edited" | "confirmed" | "cancelled" | "expired" | null;
}

export interface LabDraftCandidate {
  page_number: number;
  row_index: number;
  extraction_tier: "embedded_text" | "ocr" | "vision";
  coordinate_space: "pdf_points" | "rendered_pixels";
  parsed: boolean;
  analyte_name: string | null;
  original_value: string | null;
  original_unit: string | null;
  original_reference_range: string | null;
  source_text: string;
  confidence: number;
  flags: string[];
  requires_confirmation: true;
}

export interface LabExtractionDraft {
  category: "ai_draft";
  draft_id: string;
  document_id: string;
  state: "pending" | "edited" | "confirmed" | "cancelled" | "expired";
  schema_version: string;
  prompt_version: string;
  model_name: string | null;
  candidates: LabDraftCandidate[];
}

export interface LabCandidateConfirmation {
  candidate_index: number;
  included: boolean;
  analyte_name: string;
  original_value: string;
  original_unit: string | null;
  original_reference_range: string | null;
}

export interface LabDocumentConfirmation {
  specimen_time: { local_time: string; timezone: string };
  report_time: { local_time: string; timezone: string };
  laboratory_name: string | null;
  accession_id: string | null;
  specimen_type: string | null;
  report_status: string | null;
  candidates: LabCandidateConfirmation[];
}

export interface LabConfirmationResult {
  category: "fact";
  panel_id: string;
  created: boolean;
  result_count: number;
}

export interface LabDeletionPreview {
  document_id: string;
  mode: "unconfirmed_upload" | "confirmed_report";
  requires_password: boolean;
  confirmation_phrase: string;
  extraction_draft_ids: string[];
  panel_ids: string[];
  result_ids: string[];
  derived_result_count: number;
  trend_point_count: number;
  ai_analysis_ids: string[];
  report_snapshot_ids: string[];
  report_artifact_ids: string[];
  page_preview_count: number;
  private_storage_artifact_count: number;
  backups_may_retain_until_expiry: true;
}

export interface LabDeletionAccepted {
  status: "deletion_queued" | "already_deleted";
  document_id: string;
  cleanup_task_count: number;
}

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
  sortOrder: "asc" | "desc";
}

export function getTimeline(filters: TimelineFilters): Promise<Timeline> {
  const params = new URLSearchParams({ timezone: filters.timezone });
  if (filters.type !== "") params.set("types", filters.type);
  if (filters.dateFrom !== "") params.set("local_date_from", filters.dateFrom);
  if (filters.dateTo !== "") params.set("local_date_to", filters.dateTo);
  if (filters.includeSensitive) params.set("include_sensitive", "true");
  params.set("sort_order", filters.sortOrder);
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

export function getBloodPressure(includeSuperseded = false): Promise<BloodPressure[]> {
  return apiRequest<BloodPressure[]>(`/blood-pressure${includeSuperseded ? "?include_superseded=true" : ""}`);
}

export function createBloodPressure(payload: BloodPressureInput): Promise<BloodPressure> {
  return apiRequest<BloodPressure>("/blood-pressure", { method: "POST", body: JSON.stringify(payload) });
}

export function correctBloodPressure(id: string, payload: BloodPressureCorrectionInput): Promise<BloodPressure> {
  return apiRequest<BloodPressure>(`/blood-pressure/${id}/correct`, { method: "POST", body: JSON.stringify(payload) });
}

export function getWeight(includeSuperseded = false): Promise<Weight[]> {
  return apiRequest<Weight[]>(`/weight${includeSuperseded ? "?include_superseded=true" : ""}`);
}

export function getLabResults(): Promise<LabResult[]> {
  return apiRequest<LabResult[]>("/labs/results");
}

export function getLabDocuments(): Promise<LabDocument[]> {
  return apiRequest<LabDocument[]>("/labs/documents");
}

export function getLabDocument(documentId: string): Promise<LabDocument> {
  return apiRequest<LabDocument>(`/labs/documents/${documentId}`);
}

export function getLabExtraction(documentId: string): Promise<LabExtractionDraft> {
  return apiRequest<LabExtractionDraft>(`/labs/documents/${documentId}/extraction`);
}

export function uploadLabDocument(file: File): Promise<LabDocument> {
  const body = new FormData();
  body.set("file", file);
  return apiRequest<LabDocument>("/labs/documents", { method: "POST", body });
}

export function confirmLabDocument(
  documentId: string,
  payload: LabDocumentConfirmation,
): Promise<LabConfirmationResult> {
  return apiRequest<LabConfirmationResult>(`/labs/documents/${documentId}/confirm`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getLabDeletionPreview(documentId: string): Promise<LabDeletionPreview> {
  return apiRequest<LabDeletionPreview>(`/labs/documents/${documentId}/deletion-preview`);
}

export function deleteLabDocument(
  documentId: string,
  payload: { password: string | null; confirmation: string },
): Promise<LabDeletionAccepted> {
  return apiRequest<LabDeletionAccepted>(`/labs/documents/${documentId}`, {
    method: "DELETE",
    body: JSON.stringify(payload),
  });
}

export function createWeight(payload: WeightInput): Promise<Weight> {
  return apiRequest<Weight>("/weight", { method: "POST", body: JSON.stringify(payload) });
}

export function correctWeight(id: string, payload: WeightCorrectionInput): Promise<Weight> {
  return apiRequest<Weight>(`/weight/${id}/correct`, { method: "POST", body: JSON.stringify(payload) });
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

export function getMedications(): Promise<Medication[]> {
  return apiRequest<Medication[]>("/medications");
}

export function createMedication(payload: MedicationInput): Promise<Medication> {
  return apiRequest<Medication>("/medications", { method: "POST", body: JSON.stringify(payload) });
}

export function createRegimen(payload: RegimenInput): Promise<RegimenVersion> {
  return apiRequest<RegimenVersion>("/regimens", { method: "POST", body: JSON.stringify(payload) });
}

export function updateRegimenDraft(versionId: string, payload: RegimenInput): Promise<RegimenVersion> {
  return apiRequest<RegimenVersion>(`/regimens/${versionId}`, { method: "PUT", body: JSON.stringify(payload) });
}

export function approveRegimen(versionId: string, payload: RegimenApprovalInput): Promise<RegimenVersion> {
  return apiRequest<RegimenVersion>(`/regimens/${versionId}/approve`, { method: "POST", body: JSON.stringify(payload) });
}

export function retireRegimen(versionId: string): Promise<RegimenVersion> {
  return apiRequest<RegimenVersion>(`/regimens/${versionId}/retire`, { method: "POST" });
}

export function getActiveRegimen(): Promise<RegimenVersion | null> {
  return apiRequest<RegimenVersion | null>("/regimens/active");
}

export function getRegimenDiff(olderId: string, newerId: string): Promise<Record<string, string[]>> {
  return apiRequest<Record<string, string[]>>(`/regimens/${olderId}/diff/${newerId}`);
}

export async function deleteRegimen(versionId: string): Promise<void> {
  await apiRequest<unknown>(`/regimens/${versionId}`, {
    method: "DELETE",
  });
}

export function getAnalyticsSummary(dateFrom: string, dateTo: string, timezone: string): Promise<AnalyticsSummary> {
  const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo, timezone });
  return apiRequest<AnalyticsSummary>(`/analytics/summary?${params.toString()}`);
}

export function getDataQuality(): Promise<DataQuality> {
  return apiRequest<DataQuality>("/data-quality");
}

export function getGarminStatus(): Promise<GarminStatus> {
  return apiRequest<GarminStatus>("/integrations/garmin/status");
}

export function getGarminRecords(): Promise<GarminRecords> {
  return apiRequest<GarminRecords>("/integrations/garmin/records");
}

export function getGarminDisconnectPreview(): Promise<GarminDisconnectPreview> {
  return apiRequest<GarminDisconnectPreview>("/integrations/garmin/disconnect-preview");
}

export interface GarminSyncRequest {
  job_id: string;
  status: string;
  disposition: "queued" | "refresh_queued" | "coalesced_active" | "cooldown_reused" | "idempotent_replay";
  requested_start_date: string;
  requested_end_date: string;
  cooldown_until: string | null;
}

export function requestGarminSync(idempotencyKey: string, refresh = false): Promise<GarminSyncRequest> {
  return apiRequest<GarminSyncRequest>(`/integrations/garmin/sync${refresh ? "?refresh=true" : ""}`, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
  });
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

export type IntegrationDeletionResult = components["schemas"]["IntegrationDeletionResponse"];

export function disconnectIntegration(provider: "garmin" | "telegram" | "weather", password: string, deleteData: boolean, confirmation?: string): Promise<IntegrationDeletionResult> {
  return apiRequest<IntegrationDeletionResult>(`/privacy/integrations/${provider}`, { method: "DELETE", body: JSON.stringify({ password, delete_data: deleteData, confirmation }) });
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

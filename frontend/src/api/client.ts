import type { components } from "./schema";
import { sessionStore, type ActiveSession } from "./session";

type LoginRequest = components["schemas"]["LoginRequest"];
type LoginResponse = components["schemas"]["LoginResponse"];
type WhoAmI = components["schemas"]["WhoAmI"];
export type PlanComparisonDay = components["schemas"]["PlanComparisonDay"];
export type Episode = components["schemas"]["EpisodeOut"];
export type EpisodePage = components["schemas"]["EpisodePage"];
export type EpisodeInput = components["schemas"]["EpisodeIn"];
export type EpisodeUpdate = components["schemas"]["EpisodeUpdate"];
export type Injection = components["schemas"]["InjectionOut"];
export type InjectionPage = components["schemas"]["InjectionPage"];
export type Dose = components["schemas"]["DoseOut"];
export type DosePage = components["schemas"]["DosePage"];
export type DoseInput = components["schemas"]["DoseIn"];
export type DoseCorrectionInput = components["schemas"]["DoseCorrectionIn"];
export type Timeline = components["schemas"]["TimelinePage"];
export type Symptom = components["schemas"]["SymptomOut"];
export type SymptomPage = components["schemas"]["SymptomPage"];
export type SymptomInput = components["schemas"]["SymptomIn"];
export type SymptomCorrectionInput = components["schemas"]["SymptomCorrectionIn"];
export type DiaryEntry = components["schemas"]["DiaryOut"];
export type DiaryPage = components["schemas"]["DiaryPage"];
export type Meal = components["schemas"]["MealOut"];
export type MealPage = components["schemas"]["MealPage"];
export type MealInput = components["schemas"]["MealIn"];
export type MealCorrectionInput = components["schemas"]["MealCorrectionIn"];
export type LifeEvent = components["schemas"]["LifeEventOut"];
export type LifeEventPage = components["schemas"]["LifeEventPage"];
export type RegimenVersion = components["schemas"]["RegimenVersionOut"];
export type RegimenVersionPage = components["schemas"]["RegimenVersionPage"];
export type RegimenInput = components["schemas"]["RegimenVersionIn"];
export type RegimenApprovalInput = components["schemas"]["RegimenApprovalIn"];
export type Medication = components["schemas"]["MedicationOut"];
export type MedicationInput = components["schemas"]["MedicationIn"];
export type AnalyticsSummary = components["schemas"]["AnalyticsSummaryOut"];
export type SimpleExposureCurve = components["schemas"]["SteroidExposureCurveOut"];
export type PhysiologicalCortisolCurve = components["schemas"]["PhysiologicalCortisolCurveOut"];
export type WakeFreeCortisolCurve = components["schemas"]["WakeFreeCortisolCurveOut"];
export type SteroidExposureCurve = SimpleExposureCurve | PhysiologicalCortisolCurve | WakeFreeCortisolCurve;
export type HealthCurveModel =
  | "hc-exposure-v1"
  | "hc-physiology-v2"
  | "hc-wake-free-v3"
  | "hc-mixed-route-free-v4";
export type DailyPatterns = components["schemas"]["DailyPatternsOut"];
export type PatternAnalysis = components["schemas"]["PatternAnalysisOut"];
export type PatternAnalysisGeneration = components["schemas"]["PatternAnalysisGenerationOut"];
export type DayAnalysis = components["schemas"]["DayAnalysisOut"];
export type DayAnalysisGeneration = components["schemas"]["DayAnalysisGenerationOut"];
export type DataQuality = components["schemas"]["DataQualityOut"];
export type ReportSummary = components["schemas"]["ReportOut"];
export type ReportPage = components["schemas"]["ReportPage"];
export type ReportPreview = components["schemas"]["ReportPreviewOut"];
export type ReportCreate = components["schemas"]["ReportCreateRequest"];
export type ContextEvent = components["schemas"]["ContextOut"];
export type ContextPage = components["schemas"]["ContextPage"];
export type ContextInput = components["schemas"]["ContextIn"];
export type BloodPressure = components["schemas"]["BloodPressureOut"];
export type BloodPressurePage = components["schemas"]["BloodPressurePage"];
export type BloodPressureInput = components["schemas"]["BloodPressureIn"];
export type BloodPressureCorrectionInput = components["schemas"]["BloodPressureCorrectionIn"];
export type Weight = components["schemas"]["WeightOut"];
export type WeightPage = components["schemas"]["WeightPage"];
export type WeightInput = components["schemas"]["WeightIn"];
export type WeightCorrectionInput = components["schemas"]["WeightCorrectionIn"];
export type Temperature = components["schemas"]["TemperatureOut"];
export type TemperaturePage = components["schemas"]["TemperaturePage"];
export type TemperatureInput = components["schemas"]["TemperatureIn"];
export type TemperatureCorrectionInput = components["schemas"]["TemperatureCorrectionIn"];
export type LabResult = components["schemas"]["LabResultOut"];
export type LabResultPage = components["schemas"]["LabResultPage"];
export type PageMetadata = components["schemas"]["PageMetadata"];
export type GarminStatus = components["schemas"]["GarminStatusOut"];
export type GarminRecord = components["schemas"]["GarminRecordOut"];
export type GarminRecords = components["schemas"]["GarminRecordsOut"];
export type GarminDisconnectPreview = components["schemas"]["GarminDisconnectPreviewOut"];
export type ChatConversation = components["schemas"]["ChatConversationOut"];
export type ChatConversationPage = components["schemas"]["ChatConversationPage"];
export type ChatMessage = components["schemas"]["ChatMessageOut"];
export type ChatMessagePage = components["schemas"]["ChatMessagePage"];
export type ChatMessageStaleness = components["schemas"]["ChatMessageStalenessOut"];

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

export interface LabDocumentPage {
  items: LabDocument[];
  page: PageMetadata;
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
  detail?: unknown;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly code: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export class AnalysisRequestTimeoutError extends Error {
  constructor(timeoutSeconds = ANALYSIS_REQUEST_TIMEOUT_SECONDS) {
    super(`The analysis request did not finish within ${timeoutSeconds.toString()} seconds.`);
    this.name = "AnalysisRequestTimeoutError";
  }
}

export class AnalysisRequestCancelledError extends Error {
  constructor() {
    super("The browser stopped waiting for the private-model request.");
    this.name = "AnalysisRequestCancelledError";
  }
}

export const ANALYSIS_REQUEST_TIMEOUT_SECONDS = 75;
export const PATTERN_ANALYSIS_REQUEST_TIMEOUT_SECONDS = 135;

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
  let code: string | null = null;
  try {
    const body = (await response.json()) as ApiErrorBody;
    if (typeof body.detail === "string") detail = body.detail;
    if (typeof body.detail === "object" && body.detail !== null && !Array.isArray(body.detail)) {
      const candidate = (body.detail as { code?: unknown }).code;
      if (typeof candidate === "string") code = candidate;
    }
    if (Array.isArray(body.detail)) {
      const issues = body.detail.flatMap((item) => {
        if (typeof item !== "object" || item === null) return [];
        const issue = item as { loc?: unknown; msg?: unknown };
        if (typeof issue.msg !== "string") return [];
        const path = Array.isArray(issue.loc)
          ? issue.loc.filter((part) => part !== "body").map(String).join(" → ")
          : "";
        const message = issue.msg.replace(/^Value error,\s*/u, "");
        return [`${path === "" ? "Entry" : path}: ${message}`];
      });
      if (issues.length > 0) detail = issues.join("; ");
    }
  } catch {
    // Privacy-safe fallback: never surface server response bodies as HTML or diagnostics.
  }
  return new ApiError(response.status, detail, code);
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

export function getOpenEpisodes(): Promise<EpisodePage> {
  return apiRequest<EpisodePage>("/stress-episodes?page=1&status_filter=open");
}

export interface RecordedHistoryFilters {
  dateFrom: string;
  dateTo: string;
  timezone: string;
}

function recordedHistoryQuery(filters: RecordedHistoryFilters, page: number): URLSearchParams {
  const params = new URLSearchParams({ timezone: filters.timezone, page: page.toString() });
  if (filters.dateFrom !== "") params.set("local_date_from", filters.dateFrom);
  if (filters.dateTo !== "") params.set("local_date_to", filters.dateTo);
  return params;
}

export function getEpisodes(filters: RecordedHistoryFilters, page = 1, status?: "open" | "resolved", episodeId?: string): Promise<EpisodePage> {
  const params = recordedHistoryQuery(filters, page);
  if (status !== undefined) params.set("status_filter", status);
  if (episodeId !== undefined) params.set("episode_id", episodeId);
  return apiRequest<EpisodePage>(`/stress-episodes?${params.toString()}`);
}

export function getEmergencyInjections(filters: RecordedHistoryFilters, page = 1): Promise<InjectionPage> {
  return apiRequest<InjectionPage>(`/emergency-injections?${recordedHistoryQuery(filters, page).toString()}`);
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

export function getDoses(filters: RecordedHistoryFilters, page = 1): Promise<DosePage> {
  return apiRequest<DosePage>(`/doses?${recordedHistoryQuery(filters, page).toString()}`);
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
  page: number;
}

export function getTimeline(filters: TimelineFilters): Promise<Timeline> {
  const params = new URLSearchParams({ timezone: filters.timezone });
  if (filters.type !== "") params.set("types", filters.type);
  if (filters.dateFrom !== "") params.set("local_date_from", filters.dateFrom);
  if (filters.dateTo !== "") params.set("local_date_to", filters.dateTo);
  if (filters.includeSensitive) params.set("include_sensitive", "true");
  params.set("sort_order", filters.sortOrder);
  params.set("page", filters.page.toString());
  return apiRequest<Timeline>(`/timeline?${params.toString()}`);
}

export function getContextEvents(filters: RecordedHistoryFilters, page = 1): Promise<ContextPage> {
  return apiRequest<ContextPage>(`/context-events?${recordedHistoryQuery(filters, page).toString()}`);
}

export function createContextEvent(payload: ContextInput): Promise<ContextEvent> {
  return apiRequest<ContextEvent>("/context-events", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export interface HealthDataFilters {
  dateFrom: string;
  dateTo: string;
  timezone: string;
}

function healthDataQuery(filters: HealthDataFilters, page: number): string {
  const params = new URLSearchParams({ timezone: filters.timezone, page: page.toString() });
  if (filters.dateFrom !== "") params.set("local_date_from", filters.dateFrom);
  if (filters.dateTo !== "") params.set("local_date_to", filters.dateTo);
  return params.toString();
}

export function getBloodPressure(filters: HealthDataFilters, page = 1): Promise<BloodPressurePage> {
  return apiRequest<BloodPressurePage>(`/blood-pressure?${healthDataQuery(filters, page)}`);
}

export function createBloodPressure(payload: BloodPressureInput): Promise<BloodPressure> {
  return apiRequest<BloodPressure>("/blood-pressure", { method: "POST", body: JSON.stringify(payload) });
}

export function correctBloodPressure(id: string, payload: BloodPressureCorrectionInput): Promise<BloodPressure> {
  return apiRequest<BloodPressure>(`/blood-pressure/${id}/correct`, { method: "POST", body: JSON.stringify(payload) });
}

export function getWeight(filters: HealthDataFilters, page = 1): Promise<WeightPage> {
  return apiRequest<WeightPage>(`/weight?${healthDataQuery(filters, page)}`);
}

export function getLabResults(filters: RecordedHistoryFilters, page = 1): Promise<LabResultPage> {
  return apiRequest<LabResultPage>(`/labs/results?${recordedHistoryQuery(filters, page).toString()}`);
}

export function getLabDocuments(filters: RecordedHistoryFilters, page = 1): Promise<LabDocumentPage> {
  return apiRequest<LabDocumentPage>(`/labs/documents?${recordedHistoryQuery(filters, page).toString()}`);
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

export function getTemperature(filters: HealthDataFilters, page = 1): Promise<TemperaturePage> {
  return apiRequest<TemperaturePage>(`/temperature?${healthDataQuery(filters, page)}`);
}

export function createTemperature(payload: TemperatureInput): Promise<Temperature> {
  return apiRequest<Temperature>("/temperature", { method: "POST", body: JSON.stringify(payload) });
}

export function correctTemperature(
  id: string,
  payload: TemperatureCorrectionInput,
): Promise<Temperature> {
  return apiRequest<Temperature>(`/temperature/${id}/correct`, {
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

export interface SymptomsDiaryFilters {
  dateFrom: string;
  dateTo: string;
  timezone: string;
  includeSensitive: boolean;
}

function symptomsDiaryQuery(filters: SymptomsDiaryFilters, page: number): URLSearchParams {
  const params = new URLSearchParams({ timezone: filters.timezone, page: page.toString() });
  if (filters.dateFrom !== "") params.set("local_date_from", filters.dateFrom);
  if (filters.dateTo !== "") params.set("local_date_to", filters.dateTo);
  if (filters.includeSensitive) params.set("include_sensitive", "true");
  return params;
}

export function getSymptoms(filters: SymptomsDiaryFilters, page = 1): Promise<SymptomPage> {
  const params = symptomsDiaryQuery(filters, page);
  params.delete("include_sensitive");
  return apiRequest<SymptomPage>(`/symptoms?${params.toString()}`);
}

export function createSymptom(payload: SymptomInput): Promise<Symptom> {
  return apiRequest<Symptom>("/symptoms", { method: "POST", body: JSON.stringify(payload) });
}

export function correctSymptom(id: string, payload: SymptomCorrectionInput): Promise<Symptom> {
  return apiRequest<Symptom>(`/symptoms/${id}/correct`, { method: "POST", body: JSON.stringify(payload) });
}

export async function deleteSymptom(id: string): Promise<void> {
  await apiRequest<unknown>(`/symptoms/${id}`, { method: "DELETE" });
}

export function getDiaryEntries(filters: SymptomsDiaryFilters, page = 1): Promise<DiaryPage> {
  return apiRequest<DiaryPage>(`/diary-events?${symptomsDiaryQuery(filters, page).toString()}`);
}

export function getMeals(filters: SymptomsDiaryFilters, page = 1): Promise<MealPage> {
  const params = symptomsDiaryQuery(filters, page);
  params.delete("include_sensitive");
  return apiRequest<MealPage>(`/meal-events?${params.toString()}`);
}

export function createMeal(payload: MealInput): Promise<Meal> {
  return apiRequest<Meal>("/meal-events", { method: "POST", body: JSON.stringify(payload) });
}

export function correctMeal(id: string, payload: MealCorrectionInput): Promise<Meal> {
  return apiRequest<Meal>(`/meal-events/${id}/correct`, { method: "POST", body: JSON.stringify(payload) });
}

export function getLifeEvents(filters: SymptomsDiaryFilters, page = 1): Promise<LifeEventPage> {
  return apiRequest<LifeEventPage>(`/life-events?${symptomsDiaryQuery(filters, page).toString()}`);
}

export function getRegimens(filters: RecordedHistoryFilters, page = 1): Promise<RegimenVersionPage> {
  return apiRequest<RegimenVersionPage>(`/regimens?${recordedHistoryQuery(filters, page).toString()}`);
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

export function getSteroidExposure(day: string, timezone: string, model: HealthCurveModel = "hc-mixed-route-free-v4"): Promise<SteroidExposureCurve> {
  const params = new URLSearchParams({ day, timezone, model });
  return apiRequest<SteroidExposureCurve>(`/analytics/steroid-exposure?${params.toString()}`);
}

export function getDailyPatterns(dateFrom: string, dateTo: string, timezone: string): Promise<DailyPatterns> {
  const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo, timezone });
  return apiRequest<DailyPatterns>(`/analytics/daily-patterns?${params.toString()}`);
}

async function analysisRequest<T>(
  path: string,
  externalSignal?: AbortSignal,
  timeoutSeconds = ANALYSIS_REQUEST_TIMEOUT_SECONDS,
): Promise<T> {
  const controller = new AbortController();
  const cancel = (): void => { controller.abort(new AnalysisRequestCancelledError()); };
  if (externalSignal?.aborted === true) cancel();
  else externalSignal?.addEventListener("abort", cancel, { once: true });
  const deadline = window.setTimeout(() => {
    controller.abort(new AnalysisRequestTimeoutError(timeoutSeconds));
  }, timeoutSeconds * 1000);
  try {
    return await apiRequest<T>(path, { method: "POST", signal: controller.signal });
  } catch (error: unknown) {
    if (controller.signal.reason instanceof AnalysisRequestTimeoutError) throw controller.signal.reason;
    if (controller.signal.reason instanceof AnalysisRequestCancelledError) throw controller.signal.reason;
    throw error;
  } finally {
    window.clearTimeout(deadline);
    externalSignal?.removeEventListener("abort", cancel);
  }
}

export async function getPatternAnalysis(dateFrom: string, dateTo: string, timezone: string): Promise<PatternAnalysis | null> {
  const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo, timezone });
  const analyses = await apiRequest<PatternAnalysis[]>(`/analytics/pattern-analysis?${params.toString()}`);
  return analyses.at(0) ?? null;
}

export function generatePatternAnalysis(dateFrom: string, dateTo: string, timezone: string, signal?: AbortSignal): Promise<PatternAnalysisGeneration> {
  const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo, timezone });
  return analysisRequest<PatternAnalysisGeneration>(
    `/analytics/pattern-analysis?${params.toString()}`,
    signal,
    PATTERN_ANALYSIS_REQUEST_TIMEOUT_SECONDS,
  );
}

export async function deletePatternAnalysis(analysisId: string): Promise<void> {
  await apiRequest<unknown>(`/analytics/pattern-analysis/${analysisId}`, { method: "DELETE" });
}

export function getDayAnalysis(day: string, timezone: string): Promise<DayAnalysis | null> {
  const params = new URLSearchParams({ day, timezone });
  return apiRequest<DayAnalysis | null>(`/analytics/day-analysis?${params.toString()}`);
}

export function generateDayAnalysis(day: string, timezone: string): Promise<DayAnalysisGeneration> {
  const params = new URLSearchParams({ day, timezone });
  return analysisRequest<DayAnalysisGeneration>(`/analytics/day-analysis?${params.toString()}`);
}

export async function downloadDailyPatternsCsv(dateFrom: string, dateTo: string, timezone: string): Promise<Blob> {
  const params = new URLSearchParams({ date_from: dateFrom, date_to: dateTo, timezone });
  const response = await fetch(`/api/v1/analytics/daily-patterns.csv?${params.toString()}`, {
    headers: { Accept: "text/csv" },
    credentials: "include",
  });
  if (response.status === 401) sessionStore.clear();
  if (!response.ok) throw await parseError(response);
  return response.blob();
}

async function collectPages<T>(
  fetchPage: (page: number) => Promise<{ items: T[]; totalPages: number }>,
): Promise<T[]> {
  const first = await fetchPage(1);
  const items = [...first.items];
  for (let page = 2; page <= first.totalPages; page += 1) {
    items.push(...(await fetchPage(page)).items);
  }
  return items;
}

function selectedDayParams(day: string, timezone: string, page: number): URLSearchParams {
  return new URLSearchParams({
    local_date_from: day,
    local_date_to: day,
    timezone,
    page: page.toString(),
    page_size: "100",
  });
}

export function getDailyGarminSamples(day: string, timezone: string): Promise<GarminRecord[]> {
  return collectPages(async (page) => {
    const params = new URLSearchParams({ day, timezone, page: page.toString(), page_size: "100" });
    const response = await apiRequest<GarminRecords>(`/integrations/garmin/samples?${params.toString()}`);
    return { items: response.records, totalPages: response.page.total_pages };
  });
}

export function getDailyGarminSleep(day: string, timezone: string): Promise<GarminRecord[]> {
  return collectPages(async (page) => {
    const params = new URLSearchParams({ day, timezone, page: page.toString(), page_size: "100" });
    const response = await apiRequest<GarminRecords>(`/integrations/garmin/sleep?${params.toString()}`);
    return { items: response.records, totalPages: response.page.total_pages };
  });
}

export async function getDailyGarminContext(day: string, timezone: string): Promise<GarminRecord[]> {
  const [dailyRecords, samples, sleep] = await Promise.all([
    collectPages(async (page) => {
      const response = await apiRequest<GarminRecords>(
        `/integrations/garmin/records?${selectedDayParams(day, timezone, page).toString()}`,
      );
      return {
        items: response.records.filter((record) => record.kind === "daily"),
        totalPages: response.page.total_pages,
      };
    }),
    getDailyGarminSamples(day, timezone),
    getDailyGarminSleep(day, timezone),
  ]);
  const currentById = new Map(
    [...dailyRecords, ...samples, ...sleep].map((record) => [record.id, record]),
  );
  return [...currentById.values()].sort(
    (left, right) => Date.parse(left.time.occurred_at) - Date.parse(right.time.occurred_at),
  );
}

export function getDailySymptoms(day: string, timezone: string): Promise<Symptom[]> {
  return collectPages(async (page) => {
    const response = await apiRequest<SymptomPage>(`/symptoms?${selectedDayParams(day, timezone, page).toString()}`);
    return { items: response.items, totalPages: response.page.total_pages };
  });
}

export function getDailyBloodPressure(day: string, timezone: string): Promise<BloodPressure[]> {
  return collectPages(async (page) => {
    const response = await apiRequest<BloodPressurePage>(`/blood-pressure?${selectedDayParams(day, timezone, page).toString()}`);
    return { items: response.items, totalPages: response.page.total_pages };
  });
}

export function getDailyTemperature(day: string, timezone: string): Promise<Temperature[]> {
  return collectPages(async (page) => {
    const response = await apiRequest<TemperaturePage>(`/temperature?${selectedDayParams(day, timezone, page).toString()}`);
    return { items: response.items, totalPages: response.page.total_pages };
  });
}

export function getDailyEpisodes(day: string, timezone: string): Promise<Episode[]> {
  return collectPages(async (page) => {
    const params = selectedDayParams(day, timezone, page);
    params.set("overlaps_window", "true");
    const response = await apiRequest<EpisodePage>(`/stress-episodes?${params.toString()}`);
    return { items: response.items, totalPages: response.page.total_pages };
  });
}

export function getDataQuality(page = 1): Promise<DataQuality> {
  return apiRequest<DataQuality>(`/data-quality?page=${page.toString()}`);
}

export async function acknowledgeGarminSyncFinding(syncRunId: string): Promise<void> {
  await apiRequest<unknown>(`/data-quality/garmin-syncs/${encodeURIComponent(syncRunId)}/acknowledge`, {
    method: "POST",
  });
}

export function getGarminStatus(): Promise<GarminStatus> {
  return apiRequest<GarminStatus>("/integrations/garmin/status");
}

export function getGarminRecords(filters: HealthDataFilters, page = 1): Promise<GarminRecords> {
  return apiRequest<GarminRecords>(`/integrations/garmin/records?${healthDataQuery(filters, page)}`);
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

export function getReports(filters: RecordedHistoryFilters, page = 1): Promise<ReportPage> {
  return apiRequest<ReportPage>(`/reports?${recordedHistoryQuery(filters, page).toString()}`);
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

export interface PrivateExport {
  id: string;
  job_id: string;
  status: "queued" | "running" | "completed" | "dead_letter" | "expired";
  include_ai: boolean;
  include_sensitive: boolean;
  processed_rows: number;
  total_rows: number | null;
  progress_percent: number | null;
  attempt_count: number;
  max_attempts: number;
  next_attempt_at: string | null;
  last_error_code: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  expires_at: string;
  download_url: string | null;
  sha256: string | null;
  byte_size: number | null;
}

export interface PrivateExportPage {
  items: PrivateExport[];
  page: PageMetadata;
}

export function requestPrivateExport(password: string, includeAi: boolean, includeSensitive: boolean, idempotencyKey: string): Promise<PrivateExport> {
  return apiRequest<PrivateExport>("/privacy/export", {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ password, include_ai: includeAi, include_sensitive: includeSensitive }),
  });
}

export function getPrivateExports(): Promise<PrivateExportPage> {
  return apiRequest<PrivateExportPage>("/privacy/exports?page=1&page_size=10");
}

export function getChatConversations(page = 1): Promise<ChatConversationPage> {
  return apiRequest<ChatConversationPage>(`/chat/conversations?page=${page.toString()}&page_size=50`);
}

export function createChatConversation(includeSensitiveText = false): Promise<ChatConversation> {
  return apiRequest<ChatConversation>("/chat/conversations", {
    method: "POST",
    body: JSON.stringify({ title: "New conversation", include_sensitive_text: includeSensitiveText }),
  });
}

export function updateChatConversation(id: string, payload: { title?: string; include_sensitive_text?: boolean }): Promise<ChatConversation> {
  return apiRequest<ChatConversation>(`/chat/conversations/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function deleteChatConversation(id: string): Promise<void> {
  await apiRequest<unknown>(`/chat/conversations/${id}`, { method: "DELETE" });
}

export async function deleteAllChatConversations(): Promise<void> {
  await apiRequest<unknown>("/chat/conversations", { method: "DELETE" });
}

export function getChatMessages(conversationId: string): Promise<ChatMessagePage> {
  return apiRequest<ChatMessagePage>(`/chat/conversations/${conversationId}/messages?page=1&page_size=100`);
}

export function sendChatMessage(conversationId: string, body: string, clientMessageId: string): Promise<ChatMessage> {
  return apiRequest<ChatMessage>(`/chat/conversations/${conversationId}/messages`, {
    method: "POST",
    body: JSON.stringify({ body, client_message_id: clientMessageId }),
  });
}

export function cancelChatMessage(messageId: string): Promise<ChatMessage> {
  return apiRequest<ChatMessage>(`/chat/messages/${messageId}/cancel`, { method: "POST" });
}

export function getChatMessageStaleness(messageId: string): Promise<ChatMessageStaleness> {
  return apiRequest<ChatMessageStaleness>(`/chat/messages/${messageId}/staleness`);
}

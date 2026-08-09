import type { components } from "./schema";
import { sessionStore, type ActiveSession } from "./session";

type LoginRequest = components["schemas"]["LoginRequest"];
type LoginResponse = components["schemas"]["LoginResponse"];
type WhoAmI = components["schemas"]["WhoAmI"];
export type PlanComparisonDay = components["schemas"]["PlanComparisonDay"];
export type Episode = components["schemas"]["EpisodeOut"];
export type Dose = components["schemas"]["DoseOut"];
export type DoseInput = components["schemas"]["DoseIn"];
export type Timeline = components["schemas"]["TimelinePage"];

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

export function recordDose(payload: DoseInput): Promise<Dose> {
  return apiRequest<Dose>("/doses", {
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

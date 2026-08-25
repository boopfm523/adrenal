import type { DailyHealthCurveData } from "../components/DailyHealthCurve";

export const PUBLIC_SCHEMA_VERSION = "healthcurve-public-v1";

export interface PublicManifest {
  schema_version: typeof PUBLIC_SCHEMA_VERSION;
  timezone: string;
  newest_date: string;
  dates: string[];
}

export interface PublicDayPayload {
  schema_version: typeof PUBLIC_SCHEMA_VERSION;
  date: string;
  timezone: string;
  curve: DailyHealthCurveData["exposure"];
  garmin: DailyHealthCurveData["garmin"];
  symptoms: DailyHealthCurveData["symptoms"];
  blood_pressure: DailyHealthCurveData["bloodPressure"];
  temperature: DailyHealthCurveData["temperature"];
  event_context_blood_pressure: NonNullable<DailyHealthCurveData["eventContextBloodPressure"]>;
  event_context_temperature: NonNullable<DailyHealthCurveData["eventContextTemperature"]>;
  episodes: DailyHealthCurveData["episodes"];
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

export function parseManifest(value: unknown): PublicManifest {
  if (typeof value !== "object" || value === null) throw new Error("The date index is unavailable.");
  const candidate = value as Partial<PublicManifest>;
  if (
    candidate.schema_version !== PUBLIC_SCHEMA_VERSION
    || typeof candidate.timezone !== "string"
    || !Array.isArray(candidate.dates)
    || typeof candidate.newest_date !== "string"
  ) {
    throw new Error("The date index did not pass validation.");
  }
  const dates = candidate.dates;
  if (
    dates.length === 0
    || !dates.every((day) => typeof day === "string" && ISO_DATE.test(day))
    || candidate.newest_date !== dates.at(-1)
    || new Set(dates).size !== dates.length
    || dates.some((day, index) => index > 0 && day <= (dates[index - 1] ?? ""))
  ) throw new Error("The date index did not pass validation.");
  return candidate as PublicManifest;
}

export function parseDay(value: unknown, expectedDate: string, timezone: string): PublicDayPayload {
  if (typeof value !== "object" || value === null) throw new Error("The selected day is unavailable.");
  const candidate = value as Record<string, unknown>;
  if (
    candidate.schema_version !== PUBLIC_SCHEMA_VERSION
    || candidate.date !== expectedDate
    || candidate.timezone !== timezone
    || typeof candidate.curve !== "object"
    || candidate.curve === null
    || !Array.isArray(candidate.garmin)
    || !Array.isArray(candidate.symptoms)
    || !Array.isArray(candidate.blood_pressure)
    || !Array.isArray(candidate.temperature)
    || !Array.isArray(candidate.event_context_blood_pressure)
    || !Array.isArray(candidate.event_context_temperature)
    || !Array.isArray(candidate.episodes)
  ) {
    throw new Error("The selected day did not pass validation.");
  }
  return candidate as unknown as PublicDayPayload;
}

export function curveData(payload: PublicDayPayload): DailyHealthCurveData {
  return {
    exposure: payload.curve,
    garmin: payload.garmin,
    symptoms: payload.symptoms,
    bloodPressure: payload.blood_pressure,
    temperature: payload.temperature,
    eventContextBloodPressure: payload.event_context_blood_pressure,
    eventContextTemperature: payload.event_context_temperature,
    episodes: payload.episodes,
  };
}

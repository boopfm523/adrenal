import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import {
  createContextEvent,
  deleteContextEvent,
  getContextEvents,
  type ContextEvent,
  type ContextInput,
  type RecordedHistoryFilters,
} from "../api/client";
import { useAuth } from "../auth/context";
import { formatDecimal, formatMeasurement } from "../format";
import { timezoneAbbreviation } from "../time";
import { PaginationControls } from "./PaginationControls";

type Precision = ContextInput["location_precision"];

function formValue(data: FormData, name: string): string {
  const value = data.get(name);
  return typeof value === "string" ? value : "";
}

function optionalValue(data: FormData, name: string): string | null {
  const value = formValue(data, name).trim();
  return value === "" ? null : value;
}

function contextTitle(event: ContextEvent): string {
  if (event.location_precision === "coarse") {
    return event.coarse_location_label ?? "Coarse location";
  }
  if (event.location_precision === "exact") return "Exact location (consent recorded)";
  return `Timezone context: ${timezoneAbbreviation(event.time.timezone, event.time.occurred_at)}`;
}

interface ContextViewState extends RecordedHistoryFilters { page: number }
function contextViewFromSearch(search: string, profileTimezone: string): ContextViewState { const params = new URLSearchParams(search); const page = params.get("context_page") ?? ""; return { dateFrom: params.get("context_date_from") ?? "", dateTo: params.get("context_date_to") ?? "", timezone: params.get("context_timezone") ?? profileTimezone, page: /^\d+$/.test(page) && Number(page) >= 1 ? Number(page) : 1 }; }
function searchWithContext(current: URLSearchParams, view: ContextViewState): URLSearchParams { const params = new URLSearchParams(current); for (const name of ["context_date_from", "context_date_to", "context_timezone", "context_page"]) params.delete(name); if (view.dateFrom !== "") params.set("context_date_from", view.dateFrom); if (view.dateTo !== "") params.set("context_date_to", view.dateTo); params.set("context_timezone", view.timezone); if (view.page > 1) params.set("context_page", view.page.toString()); return params; }

function WeatherDetails({ event }: { event: ContextEvent }): React.JSX.Element {
  if (event.weather_provider === undefined || event.weather_provider === null) {
    return <p className="missing-value">Weather not recorded—not zero and not inferred.</p>;
  }
  return (
    <div>
      <p><strong>Weather source:</strong> {event.weather_provider} observation at {event.weather_observed_at ?? "time not recorded"}</p>
      <dl className="provenance-grid">
        <div><dt>Conditions</dt><dd>{event.conditions ?? "Not recorded"}</dd></div>
        <div><dt>Temperature</dt><dd>{event.temperature === undefined || event.temperature === null ? "Not recorded" : `${formatDecimal(event.temperature)} °${event.temperature_unit?.toUpperCase() ?? "unit not recorded"}`}</dd></div>
        <div><dt>Humidity</dt><dd>{event.humidity_percent === undefined || event.humidity_percent === null ? "Not recorded" : `${formatDecimal(event.humidity_percent)}%`}</dd></div>
        <div><dt>Pressure</dt><dd>{event.pressure === undefined || event.pressure === null ? "Not recorded" : formatMeasurement(event.pressure, event.pressure_unit)}</dd></div>
      </dl>
    </div>
  );
}

export function ContextSettings(): React.JSX.Element {
  const { session } = useAuth();
  const profileTimezone = session?.user.defaultTimezone ?? "UTC";
  const queryClient = useQueryClient();
  const [precision, setPrecision] = useState<Precision>("coarse");
  const [exactConsent, setExactConsent] = useState(false);
  const [includeWeather, setIncludeWeather] = useState(false);
  const [searchParams, setSearchParams] = useSearchParams();
  const appliedSearch = searchParams.toString();
  const view = useMemo(() => contextViewFromSearch(appliedSearch, profileTimezone), [appliedSearch, profileTimezone]);
  const filters = useMemo<RecordedHistoryFilters>(() => ({ dateFrom: view.dateFrom, dateTo: view.dateTo, timezone: view.timezone }), [view.dateFrom, view.dateTo, view.timezone]);
  const [draftState, setDraftState] = useState({ search: appliedSearch, filters });
  const draft = draftState.search === appliedSearch ? draftState.filters : filters;
  const setDraft = (next: RecordedHistoryFilters): void => { setDraftState({ search: appliedSearch, filters: next }); };
  const invalidRange = filters.dateFrom !== "" && filters.dateTo !== "" && filters.dateFrom > filters.dateTo;
  const [validation, setValidation] = useState<string | null>(null);
  const contexts = useQuery({ queryKey: ["context-events", filters, view.page], queryFn: () => getContextEvents(filters, view.page), enabled: !invalidRange });
  const create = useMutation({
    mutationFn: createContextEvent,
    onSuccess: () => {
      setPrecision("coarse");
      setExactConsent(false);
      setIncludeWeather(false);
      void queryClient.invalidateQueries({ queryKey: ["context-events"] });
      void queryClient.invalidateQueries({ queryKey: ["timeline"] });
    },
  });
  const remove = useMutation({
    mutationFn: ({ id, password }: { id: string; password: string }) =>
      deleteContextEvent(id, password),
    onSuccess: () => {
      if (view.page > 1 && contexts.data?.items.length === 1) setSearchParams(searchWithContext(searchParams, { ...view, page: view.page - 1 }));
      void queryClient.invalidateQueries({ queryKey: ["context-events"] });
      void queryClient.invalidateQueries({ queryKey: ["timeline"] });
    },
  });

  function submit(event: React.SyntheticEvent<HTMLFormElement>): void {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const weatherObserved = optionalValue(data, "weather_observed_at_utc");
    const payload: ContextInput = {
      time: {
        local_time: formValue(data, "local_time"),
        timezone: formValue(data, "timezone"),
      },
      location_precision: precision,
      exact_location_consent: precision === "exact" && exactConsent,
      notes: optionalValue(data, "notes"),
    };
    if (precision === "coarse") payload.coarse_location_label = formValue(data, "coarse_location_label");
    if (precision === "exact" && exactConsent) {
      payload.latitude = formValue(data, "latitude");
      payload.longitude = formValue(data, "longitude");
    }
    if (includeWeather) {
      payload.weather_provider = "manual";
      payload.weather_observed_at = weatherObserved === null ? null : `${weatherObserved}:00Z`;
      payload.conditions = optionalValue(data, "conditions");
      payload.temperature = optionalValue(data, "temperature");
      if (payload.temperature !== null) {
        payload.temperature_unit = data.get("temperature_unit") as "c" | "f";
      }
      payload.humidity_percent = optionalValue(data, "humidity_percent");
      payload.pressure = optionalValue(data, "pressure");
      if (payload.pressure !== null) {
        payload.pressure_unit = data.get("pressure_unit") as "hpa" | "inhg";
      }
    }
    create.mutate(payload, { onSuccess: () => { form.reset(); } });
  }

  return (
    <section aria-labelledby="context-heading">
      <h2 id="context-heading">Location, timezone, and weather context</h2>
      <div className="settings-card context-privacy-summary">
        <p><strong>Default: coarse location.</strong> A city, region, or your own label is usually enough for pattern review. Telegram phone locations, when deliberately shared, are rounded to 0.1° before storage; exact phone coordinates never enter HealthCurve.</p>
        <p><strong>Exact location is opt-in per record.</strong> Coordinate fields remain unavailable until you explicitly consent. HealthCurve never derives coordinates from a coarse label.</p>
        <p><strong>Retention:</strong> Context remains until you delete it or the account. Deleting a context record removes its correction chain but leaves doses, symptoms, diary entries, and other health facts intact. Encrypted backups may retain a deleted copy until expiry.</p>
        <p><strong>No external sharing:</strong> Weather entry is manual. No location is sent to a weather or geocoding provider.</p>
      </div>

      <form className="settings-card context-entry-form" onSubmit={submit}>
        <h3 className="form-wide">Record context</h3>
        <label>Experienced local date and time<input name="local_time" type="datetime-local" required /></label>
        <label>IANA timezone<input name="timezone" defaultValue={profileTimezone} required /></label>
        <label>Location precision
          <select value={precision} onChange={(event) => { const next = event.target.value as Precision; setPrecision(next); if (next !== "exact") setExactConsent(false); }}>
            <option value="coarse">Coarse label (recommended)</option>
            <option value="none">No location</option>
            <option value="exact">Exact coordinates</option>
          </select>
        </label>
        {precision === "coarse" ? <label>Coarse location label<input name="coarse_location_label" placeholder="City, region, or your own label" required maxLength={120} /></label> : null}
        {precision === "exact" ? <div className="form-wide exact-location-control">
          <label className="checkbox-label"><input type="checkbox" checked={exactConsent} onChange={(event) => { setExactConsent(event.target.checked); }} />I consent to storing exact coordinates for this record</label>
          {!exactConsent ? <p className="privacy-note">Latitude and longitude are disabled until you opt in.</p> : <div className="coordinate-fields">
            <label>Latitude<input name="latitude" type="number" min="-90" max="90" step="0.000001" required /></label>
            <label>Longitude<input name="longitude" type="number" min="-180" max="180" step="0.000001" required /></label>
          </div>}
        </div> : null}
        <label className="checkbox-label form-wide"><input type="checkbox" checked={includeWeather} onChange={(event) => { setIncludeWeather(event.target.checked); }} />Add a manual weather observation</label>
        {includeWeather ? <fieldset className="weather-fields form-wide">
          <legend>Manual weather</legend>
          <p className="privacy-note">Enter the observation time in UTC. Blank measurements remain missing; they are never treated as zero.</p>
          <label>Observed at (UTC)<input name="weather_observed_at_utc" type="datetime-local" required /></label>
          <label>Conditions<input name="conditions" maxLength={200} /></label>
          <label>Temperature<input name="temperature" type="number" step="0.01" /></label>
          <label>Temperature unit<select name="temperature_unit" defaultValue="c"><option value="c">Celsius</option><option value="f">Fahrenheit</option></select></label>
          <label>Humidity percent<input name="humidity_percent" type="number" min="0" max="100" step="0.01" /></label>
          <label>Pressure<input name="pressure" type="number" min="0.01" step="0.01" /></label>
          <label>Pressure unit<select name="pressure_unit" defaultValue="hpa"><option value="hpa">hPa</option><option value="inhg">inHg</option></select></label>
        </fieldset> : null}
        <label className="form-wide">Optional notes<textarea name="notes" maxLength={2000} /></label>
        <button type="submit" disabled={create.isPending || (precision === "exact" && !exactConsent)}>Record context</button>
        {create.isSuccess ? <p className="success-message" role="status">Context recorded.</p> : null}
        {create.isError ? <p className="error-summary" role="alert">Context was not recorded. Check the time, timezone, privacy selection, and weather provenance.</p> : null}
      </form>

      <div>
        <h3>Recorded context</h3>
        <form className="filter-panel" onSubmit={(event) => { event.preventDefault(); if (draft.dateFrom !== "" && draft.dateTo !== "" && draft.dateFrom > draft.dateTo) { setValidation("From date must be on or before Through date."); return; } setValidation(null); setSearchParams(searchWithContext(searchParams, { ...draft, page: 1 })); }}><label>From date<input type="date" value={draft.dateFrom} onChange={(event) => { setDraft({ ...draft, dateFrom: event.target.value }); }} /></label><label>Through date<input type="date" value={draft.dateTo} onChange={(event) => { setDraft({ ...draft, dateTo: event.target.value }); }} /></label><label>Context history IANA timezone<input required value={draft.timezone} onChange={(event) => { setDraft({ ...draft, timezone: event.target.value }); }} /></label>{validation === null && !invalidRange ? null : <p className="error-summary form-wide" role="alert">{validation ?? "From date must be on or before Through date."}</p>}<div className="filter-actions"><button type="submit">Apply context filters</button><button className="button-secondary" type="button" onClick={() => { setValidation(null); const reset = { dateFrom: "", dateTo: "", timezone: profileTimezone }; setDraftState({ search: "", filters: reset }); setSearchParams(searchWithContext(searchParams, { ...reset, page: 1 })); }}>Clear context filters</button></div></form>
        <p className="privacy-note">Inclusive dates use {timezoneAbbreviation(filters.timezone)}. Context is descriptive and remains separate from health facts, physician plans, and AI analysis.</p>
        {contexts.isFetching ? <p role="status">Loading recorded context…</p> : null}
        {contexts.isError ? <p className="error-summary" role="alert">Recorded context could not be loaded.</p> : null}
        {contexts.data?.page.total_items === 0 ? <p className="empty-state">No context has been recorded. This is optional.</p> : null}
        {contexts.data === undefined || contexts.data.items.length === 0 ? null : <div className="table-scroll" tabIndex={0} role="region" aria-label="Context records table"><table><caption>Recorded environmental context, latest experienced time first, with source and privacy provenance.</caption><thead><tr><th scope="col">Experienced time</th><th scope="col">Context</th><th scope="col">Location precision</th><th scope="col">Weather and notes</th><th scope="col">Provenance and actions</th></tr></thead><tbody>{contexts.data.items.map((item) => <tr key={item.id}><td className="timeline-time">{item.time.local_time.replace("T", " ").slice(0, 16)}<span>{timezoneAbbreviation(item.time.timezone, item.time.occurred_at)}</span></td><th scope="row">{contextTitle(item)}</th><td><span>{item.location_precision}</span><span>{item.location_precision === "exact" ? `${item.latitude ?? "Unavailable"}, ${item.longitude ?? "Unavailable"}` : item.coarse_location_label ?? "Not recorded"}</span></td><td><WeatherDetails event={item} />{item.notes === null ? null : <span>Notes: {item.notes}</span>}</td><td><span>{item.provenance.source_type.replaceAll("_", " ")}</span><span>{item.provenance.confirmation_state.replaceAll("_", " ")}</span><span>{item.provenance.is_correction ? `Corrected · ${item.provenance.correction_reason ?? "reason recorded"}` : "Original record"}</span><form className="context-delete-form danger-zone" onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); remove.mutate({ id: item.id, password: formValue(data, "password") }); }}><label>Current password<input name="password" type="password" required autoComplete="current-password" /></label><button type="submit" disabled={remove.isPending}>Delete this context record</button></form>{remove.isError && remove.variables.id === item.id ? <p className="error-summary" role="alert">Context was not deleted. Check your password.</p> : null}</td></tr>)}</tbody></table></div>}
        {contexts.data === undefined ? null : <PaginationControls label="Context records" metadata={contexts.data.page} onPageChange={(page) => { setSearchParams(searchWithContext(searchParams, { ...view, page })); }} />}
      </div>
    </section>
  );
}

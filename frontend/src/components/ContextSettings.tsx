import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import {
  createContextEvent,
  deleteContextEvent,
  getContextEvents,
  type ContextEvent,
  type ContextInput,
} from "../api/client";
import { useAuth } from "../auth/context";
import { formatDecimal, formatMeasurement } from "../format";
import { ContextCard } from "./CategoryCards";

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
  return `Timezone context: ${event.time.timezone}`;
}

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
  const queryClient = useQueryClient();
  const [precision, setPrecision] = useState<Precision>("coarse");
  const [exactConsent, setExactConsent] = useState(false);
  const [includeWeather, setIncludeWeather] = useState(false);
  const contexts = useQuery({ queryKey: ["context-events"], queryFn: getContextEvents });
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
        <h3>Record context</h3>
        <label>Experienced local date and time<input name="local_time" type="datetime-local" required /></label>
        <label>IANA timezone<input name="timezone" defaultValue={session?.user.defaultTimezone ?? "UTC"} required /></label>
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
        {contexts.isPending ? <p role="status">Loading recorded context…</p> : null}
        {contexts.isError ? <p className="error-summary" role="alert">Recorded context could not be loaded.</p> : null}
        {contexts.data?.length === 0 ? <p className="empty-state">No context has been recorded. This is optional.</p> : null}
        {contexts.data?.map((item) => <ContextCard key={item.id} headingLevel={4} title={contextTitle(item)} metadata={<span>{item.provenance.is_correction ? "Corrected record" : "Original record"} · {item.time.local_time.replace("T", " ").slice(0, 16)} · {item.time.timezone}</span>}>
          <dl className="provenance-grid">
            <div><dt>Precision</dt><dd>{item.location_precision}</dd></div>
            <div><dt>Location</dt><dd>{item.location_precision === "exact" ? `${item.latitude ?? "Unavailable"}, ${item.longitude ?? "Unavailable"}` : item.coarse_location_label ?? "Not recorded"}</dd></div>
          </dl>
          <WeatherDetails event={item} />
          <form className="context-delete-form danger-zone" onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); remove.mutate({ id: item.id, password: formValue(data, "password") }); }}>
            <label>Current password<input name="password" type="password" required autoComplete="current-password" /></label>
            <button type="submit" disabled={remove.isPending}>Delete this context record</button>
          </form>
          {remove.isError && remove.variables.id === item.id ? <p className="error-summary" role="alert">Context was not deleted. Check your password.</p> : null}
        </ContextCard>)}
      </div>
    </section>
  );
}

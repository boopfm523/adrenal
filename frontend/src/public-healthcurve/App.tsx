import { Alert, Loader, Paper } from "@mantine/core";
import { useEffect, useMemo, useState } from "react";

import {
  DailyHealthCurve,
  type HealthCurveVisibility,
} from "../components/DailyHealthCurve";
import {
  curveData,
  parseDay,
  parseManifest,
  type PublicDayPayload,
  type PublicManifest,
} from "./contracts";

const DEFAULT_VISIBILITY: HealthCurveVisibility = {
  exposure: true,
  stress: false,
  heart_rate: true,
  hrv: false,
  respiration_rate: false,
  steps: false,
  blood_pressure: false,
  temperature: false,
  symptoms: false,
  episodes: false,
};

function dataUrl(path: string): string {
  return new URL(`data/${path}`, document.baseURI).toString();
}

async function fetchJson(path: string): Promise<unknown> {
  const response = await fetch(dataUrl(path), {
    cache: "no-store",
    credentials: "omit",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw new Error("Published data could not be loaded.");
  return response.json() as Promise<unknown>;
}

function requestedDate(manifest: PublicManifest): string {
  const requested = new URL(window.location.href).searchParams.get("day");
  return requested !== null && manifest.dates.includes(requested)
    ? requested
    : manifest.newest_date;
}

function rememberDate(day: string): void {
  const location = new URL(window.location.href);
  location.searchParams.set("day", day);
  window.history.replaceState(null, "", location);
}

export function App(): React.JSX.Element {
  const [manifest, setManifest] = useState<PublicManifest | null>(null);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [payload, setPayload] = useState<PublicDayPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dateMessage, setDateMessage] = useState<string | null>(null);
  const [visibility, setVisibility] = useState(DEFAULT_VISIBILITY);

  useEffect(() => {
    let active = true;
    void fetchJson("manifest.json")
      .then(parseManifest)
      .then((next) => {
        if (!active) return;
        const initialDate = requestedDate(next);
        setManifest(next);
        setSelectedDate(initialDate);
        rememberDate(initialDate);
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "The date index is unavailable.");
      });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (manifest === null || selectedDate === null) return undefined;
    let active = true;
    void fetchJson(`days/${selectedDate}.json`)
      .then((value) => parseDay(value, selectedDate, manifest.timezone))
      .then((next) => {
        if (active) setPayload(next);
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "The selected day is unavailable.");
      });
    return () => { active = false; };
  }, [manifest, selectedDate]);

  const selectedIndex = manifest === null || selectedDate === null
    ? -1
    : manifest.dates.indexOf(selectedDate);
  const data = useMemo(() => payload === null ? null : curveData(payload), [payload]);

  function selectDate(day: string): void {
    if (manifest === null) return;
    if (!manifest.dates.includes(day)) {
      setDateMessage("That date is not published. Choose a completed date shown by the calendar.");
      return;
    }
    setDateMessage(null);
    setError(null);
    setPayload(null);
    setSelectedDate(day);
    rememberDate(day);
  }

  function move(offset: -1 | 1): void {
    if (manifest === null) return;
    const next = manifest.dates[selectedIndex + offset];
    if (next !== undefined) selectDate(next);
  }

  return <>
    <a className="skip-link" href="#public-healthcurve-main">Skip to the curve</a>
    <header className="public-header">
      <div className="public-brand" aria-label="HealthCurve">
        <span aria-hidden="true">H</span>
        <strong>HealthCurve</strong>
      </div>
      <p>Public read-only daily curve</p>
    </header>
    <main id="public-healthcurve-main" className="public-main">
      <section className="public-introduction" aria-labelledby="public-title">
        <h1 id="public-title">Daily HealthCurve</h1>
        <p>
          Explore completed daily curves and recorded context. This public view is read-only,
          is not medical advice, and does not accept or change health information.
        </p>
      </section>

      {manifest === null ? null : <Paper component="section" withBorder radius="lg" p="lg" className="public-calendar" aria-labelledby="calendar-title">
        <div>
          <h2 id="calendar-title">Choose a published date</h2>
          <p>Only completed dates appear. The newest completed date is selected automatically.</p>
        </div>
        <label>
          Published date
          <input
            aria-describedby={dateMessage === null ? undefined : "date-message"}
            min={manifest.dates[0]}
            max={manifest.newest_date}
            type="date"
            value={selectedDate ?? manifest.newest_date}
            onChange={(event) => { selectDate(event.currentTarget.value); }}
          />
        </label>
        <p className="public-date-count">{manifest.dates.length.toString()} completed {manifest.dates.length === 1 ? "day" : "days"} published · {manifest.timezone.replaceAll("_", " ")}</p>
        {dateMessage === null ? null : <p id="date-message" className="public-date-message" role="status">{dateMessage}</p>}
      </Paper>}

      {error === null ? null : <Alert color="red" title="Public curve unavailable">{error}</Alert>}
      {manifest === null && error === null ? <div className="public-loading"><Loader aria-label="Loading published dates" /><p>Loading published dates…</p></div> : null}
      {manifest !== null && payload === null && error === null ? <div className="public-loading"><Loader aria-label="Loading selected curve" /><p>Loading the selected curve…</p></div> : null}
      {data === null ? null : <DailyHealthCurve
        data={data}
        visible={visibility}
        onVisibleChange={setVisibility}
        onPreviousDay={() => { move(-1); }}
        onNextDay={() => { move(1); }}
        nextDayDisabled={selectedIndex < 0 || selectedIndex >= (manifest?.dates.length ?? 0) - 1}
        showSourceFingerprint={false}
      />}
    </main>
    <footer className="public-footer">
      <p>HealthCurve records and visualizes facts. It does not diagnose, establish causation, or replace clinical or emergency care.</p>
    </footer>
  </>;
}

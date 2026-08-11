import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";

import {
  correctBloodPressure,
  correctWeight,
  createBloodPressure,
  createWeight,
  getBloodPressure,
  getGarminRecords,
  getWeight,
  type BloodPressure,
  type BloodPressureCorrectionInput,
  type BloodPressureInput,
  type GarminRecord,
  type Weight,
  type WeightCorrectionInput,
  type WeightInput,
} from "../api/client";
import { useAuth } from "../auth/context";
import { AccessibleLineChart, type ChartSeries } from "../components/AccessibleLineChart";
import { Page } from "../components/Page";
import { PaginationControls } from "../components/PaginationControls";
import {
  formatDecimal,
  formatGarminDailyValue,
  garminMetricLabel,
  humanizeSource,
} from "../format";

function localNow(): string {
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60_000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 16);
}

function displayTime(value: string): string {
  return value.replace("T", " ").slice(0, 16);
}

function historyFor<T extends { id: string; provenance: { supersedes_id?: string | null } }>(record: T, byId: Map<string, T>): T[] {
  const history: T[] = [];
  let priorId = record.provenance.supersedes_id ?? null;
  while (priorId !== null) {
    const prior = byId.get(priorId);
    if (prior === undefined) break;
    history.push(prior);
    priorId = prior.provenance.supersedes_id ?? null;
  }
  return history;
}

function source(record: { provenance: { source_type: string; confirmation_state: string } }): string {
  return `${record.provenance.source_type.replaceAll("_", " ")} · ${record.provenance.confirmation_state.replaceAll("_", " ")}`;
}

function duration(seconds: number | null | undefined): string {
  if (seconds == null) return "Unavailable";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return `${hours.toString()}h ${minutes.toString()}m`;
}

function GarminRecordsTable({ records }: { records: GarminRecord[] }): React.JSX.Element {
  const ordered = [...records].sort((left, right) => left.time.occurred_at.localeCompare(right.time.occurred_at));
  return <div className="table-scroll vital-table-region" tabIndex={0} role="region" aria-label="Garmin recorded observations table">
    <table className="vital-table garmin-table">
      <caption>Garmin provider-imported recorded facts in experienced-time order. Unavailable provider values are shown as unavailable, never zero. Activity distance is miles only.</caption>
      <thead><tr><th scope="col">Experienced time</th><th scope="col">Observation</th><th scope="col">Recorded value</th><th scope="col">Duration and details</th></tr></thead>
      <tbody>{ordered.map((record) => <tr key={record.id} data-category="fact">
        <td className="timeline-time">{displayTime(record.time.local_time)}<span>{record.time.timezone}</span></td>
        <td><strong>{record.kind === "daily" ? garminMetricLabel(record.metric_type) : record.kind === "sleep" ? "Sleep" : humanizeSource(record.activity_type ?? "Activity")}</strong>{record.provenance.is_correction ? <span>{`Provider correction · ${record.provenance.correction_reason ?? "reason recorded"}`}</span> : null}</td>
        <td>{record.kind === "daily" ? formatGarminDailyValue(record.metric_type, record.value, record.unit) : record.kind === "sleep" ? <>Sleep score: {record.sleep_score ?? <span className="missing-value">Unavailable</span>}</> : <>Distance: {record.distance_miles == null ? <span className="missing-value">Unavailable</span> : `${formatDecimal(record.distance_miles)} mi`}</>}</td>
        <td>{record.kind === "daily" ? <span className="missing-value">Daily summary</span> : <>{duration(record.duration_seconds)}{record.kind === "sleep" ? <><span>Awakenings: {record.awakenings ?? "Unavailable"}</span><span>Duration source: {record.duration_source?.replaceAll("_", " ") ?? "Unavailable"}</span></> : null}</>}</td>
      </tr>)}</tbody>
    </table>
  </div>;
}

function dateRange(records: (BloodPressure | Weight)[]): string {
  if (records.length === 0) return "No recorded readings";
  const dates = records.map((record) => record.time.local_time.slice(0, 10)).sort();
  return `${dates[0] ?? ""} through ${dates.at(-1) ?? ""}`;
}

function bpSeries(records: BloodPressure[]): ChartSeries[] {
  const ordered = [...records].sort((left, right) => left.time.occurred_at.localeCompare(right.time.occurred_at));
  const values = (field: "systolic_mmhg" | "diastolic_mmhg") => ordered.map((row) => ({
    label: displayTime(row.time.local_time),
    value: row[field].toString(),
  }));
  return [
    { name: "Systolic", source: "current recorded facts", values: values("systolic_mmhg") },
    { name: "Diastolic", source: "current recorded facts", values: values("diastolic_mmhg") },
  ];
}

function weightSeries(records: Weight[]): ChartSeries[] {
  const ordered = [...records].sort((left, right) => left.time.occurred_at.localeCompare(right.time.occurred_at));
  return [{
    name: "Weight",
    source: "current recorded facts",
    values: ordered.map((row) => ({ label: displayTime(row.time.local_time), value: row.display_lb })),
  }];
}

function WeightHistoryTable({ records, byId, editing, setEditing }: {
  records: Weight[];
  byId: Map<string, Weight>;
  editing: string | null;
  setEditing: (id: string | null) => void;
}): React.JSX.Element {
  return <div className="table-scroll vital-table-region" tabIndex={0} role="region" aria-label="Weight records table">
    <table className="vital-table">
      <caption>Current recorded weight facts on a consistent pounds scale, with normalized kilograms beneath each value.</caption>
      <thead><tr><th scope="col">Experienced time</th><th scope="col">Weight</th><th scope="col">Source</th><th scope="col">Notes</th><th scope="col">Action</th></tr></thead>
      <tbody>{records.map((record) => {
        const history = historyFor(record, byId);
        return <Fragment key={record.id}>
          <tr data-category="fact">
            <td className="timeline-time">{displayTime(record.time.local_time)}<span>{record.time.timezone}</span></td>
            <td className="weight-primary"><strong>{formatDecimal(record.display_lb)} lb</strong><span className="secondary-measurement">{formatDecimal(record.normalized_kg)} kg</span></td>
            <td>{humanizeSource(record.provenance.source_type)}</td>
            <td>{record.notes ?? <span className="missing-value">None</span>}</td>
            <td>{record.provenance.is_correction ? <span>{`Corrected · ${record.provenance.correction_reason ?? "reason recorded"}`}</span> : null}<button type="button" onClick={() => { setEditing(editing === record.id ? null : record.id); }}>{editing === record.id ? "Close correction form" : "Correct weight"}</button>{history.length === 0 ? null : <details className="revision-history"><summary>Revision history ({history.length})</summary>{history.map((prior) => <article key={prior.id}><h3>Superseded value</h3><p><strong>{formatDecimal(prior.display_lb)} lb</strong> · entered {formatDecimal(prior.value)} {prior.unit}</p><p>{displayTime(prior.time.local_time)} · {prior.time.timezone}</p><p>Source: {source(prior)}</p></article>)}</details>}</td>
          </tr>
          {editing === record.id ? <tr className="correction-table-row"><td colSpan={5}><WeightCorrection record={record} close={() => { setEditing(null); }} /></td></tr> : null}
        </Fragment>;
      })}</tbody>
    </table>
  </div>;
}

function BloodPressureHistoryTable({ records, byId, editing, setEditing }: {
  records: BloodPressure[];
  byId: Map<string, BloodPressure>;
  editing: string | null;
  setEditing: (id: string | null) => void;
}): React.JSX.Element {
  const ordered = [...records].sort((left, right) => right.time.occurred_at.localeCompare(left.time.occurred_at));
  return <div className="table-scroll vital-table-region" tabIndex={0} role="region" aria-label="Blood pressure records table">
    <table className="vital-table blood-pressure-table">
      <caption>Current recorded blood-pressure facts in latest-experienced-time order, with source, confirmation, corrections, and immutable revision history.</caption>
      <thead><tr><th scope="col">Experienced time</th><th scope="col">Systolic / diastolic</th><th scope="col">Pulse</th><th scope="col">Source and confirmation</th><th scope="col">Notes</th><th scope="col">Action</th></tr></thead>
      <tbody>{ordered.map((record) => {
        const history = historyFor(record, byId);
        return <Fragment key={record.id}>
          <tr data-category="fact">
            <td className="timeline-time">{displayTime(record.time.local_time)}<span>{record.time.timezone}</span></td>
            <th scope="row" className="blood-pressure-primary">{record.systolic_mmhg.toString()}/{record.diastolic_mmhg.toString()} mmHg</th>
            <td>{record.pulse_bpm === null ? <span className="missing-value">Not recorded</span> : `${record.pulse_bpm.toString()} bpm`}</td>
            <td><span>{humanizeSource(record.provenance.source_type)}</span><span>{humanizeSource(record.provenance.confirmation_state)}</span></td>
            <td>{record.notes ?? <span className="missing-value">None</span>}</td>
            <td>{record.provenance.is_correction ? <span>{`Corrected · ${record.provenance.correction_reason ?? "reason recorded"}`}</span> : <span>Original record</span>}<button type="button" onClick={() => { setEditing(editing === record.id ? null : record.id); }}>{editing === record.id ? "Close correction form" : "Correct blood pressure"}</button>{history.length === 0 ? null : <details className="revision-history"><summary>Revision history ({history.length})</summary>{history.map((prior) => <article key={prior.id}><h3>Superseded value</h3><p><strong>{prior.systolic_mmhg}/{prior.diastolic_mmhg} mmHg</strong>{prior.pulse_bpm === null ? " · pulse not recorded" : ` · pulse ${prior.pulse_bpm.toString()} bpm`}</p><p>{displayTime(prior.time.local_time)} · {prior.time.timezone}</p><p>Source: {source(prior)}</p>{prior.notes === null ? null : <p>Notes: {prior.notes}</p>}{prior.provenance.is_correction ? <p>{`Corrected · ${prior.provenance.correction_reason ?? "reason recorded"}`}</p> : <p>Original record</p>}</article>)}</details>}</td>
          </tr>
          {editing === record.id ? <tr className="correction-table-row"><td colSpan={6}><BloodPressureCorrection record={record} close={() => { setEditing(null); }} /></td></tr> : null}
        </Fragment>;
      })}</tbody>
    </table>
  </div>;
}

function BloodPressureEntry({ timezone }: { timezone: string }): React.JSX.Element {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ systolic: "", diastolic: "", pulse: "", localTime: localNow(), notes: "" });
  const mutation = useMutation({
    mutationFn: createBloodPressure,
    onSuccess: async () => {
      setForm({ systolic: "", diastolic: "", pulse: "", localTime: localNow(), notes: "" });
      await Promise.all([queryClient.invalidateQueries({ queryKey: ["blood-pressure"] }), queryClient.invalidateQueries({ queryKey: ["timeline"] })]);
    },
  });
  function submit(event: React.SyntheticEvent<HTMLFormElement>): void {
    event.preventDefault();
    const payload: BloodPressureInput = {
      systolic_mmhg: Number(form.systolic),
      diastolic_mmhg: Number(form.diastolic),
      pulse_bpm: form.pulse === "" ? null : Number(form.pulse),
      time: { local_time: form.localTime, timezone },
      notes: form.notes === "" ? null : form.notes,
    };
    mutation.mutate(payload);
  }
  return <form className="vital-entry-form" aria-label="Record blood pressure" onSubmit={submit}>
    <h3>Blood pressure</h3>
    <label>Systolic (mmHg)<input required type="number" inputMode="numeric" min="1" max="500" value={form.systolic} onChange={(event) => { setForm({ ...form, systolic: event.target.value }); }} /></label>
    <label>Diastolic (mmHg)<input required type="number" inputMode="numeric" min="1" max="500" value={form.diastolic} onChange={(event) => { setForm({ ...form, diastolic: event.target.value }); }} /></label>
    <label>Pulse (bpm, optional)<input type="number" inputMode="numeric" min="1" max="500" value={form.pulse} onChange={(event) => { setForm({ ...form, pulse: event.target.value }); }} /></label>
    <label>Experienced local time<input required type="datetime-local" value={form.localTime} onChange={(event) => { setForm({ ...form, localTime: event.target.value }); }} /></label>
    <label className="form-wide">Notes<textarea value={form.notes} onChange={(event) => { setForm({ ...form, notes: event.target.value }); }} /></label>
    <p className="form-wide privacy-note">Timezone: {timezone}. HealthCurve records the values without interpreting them.</p>
    {mutation.isSuccess ? <p className="success-message form-wide" role="status">Blood pressure recorded.</p> : null}
    {mutation.isError ? <p className="error-summary form-wide" role="alert">Blood pressure was not saved. Check the values and time.</p> : null}
    <button className="form-wide" type="submit" disabled={mutation.isPending}>{mutation.isPending ? "Saving…" : "Record blood pressure"}</button>
  </form>;
}

function WeightEntry({ timezone }: { timezone: string }): React.JSX.Element {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ value: "", unit: "lb" as WeightInput["unit"], localTime: localNow(), notes: "" });
  const mutation = useMutation({
    mutationFn: createWeight,
    onSuccess: async () => {
      setForm({ value: "", unit: form.unit, localTime: localNow(), notes: "" });
      await Promise.all([queryClient.invalidateQueries({ queryKey: ["weight"] }), queryClient.invalidateQueries({ queryKey: ["timeline"] })]);
    },
  });
  function submit(event: React.SyntheticEvent<HTMLFormElement>): void {
    event.preventDefault();
    mutation.mutate({ value: form.value, unit: form.unit, time: { local_time: form.localTime, timezone }, notes: form.notes === "" ? null : form.notes });
  }
  return <form className="vital-entry-form" aria-label="Record weight" onSubmit={submit}>
    <h3>Weight</h3>
    <label>Value<input required type="number" inputMode="decimal" min="0.0001" max="5000" step="any" value={form.value} onChange={(event) => { setForm({ ...form, value: event.target.value }); }} /></label>
    <label>Unit<select value={form.unit} onChange={(event) => { setForm({ ...form, unit: event.target.value as WeightInput["unit"] }); }}><option value="lb">lb</option><option value="kg">kg</option></select></label>
    <label>Experienced local time<input required type="datetime-local" value={form.localTime} onChange={(event) => { setForm({ ...form, localTime: event.target.value }); }} /></label>
    <label className="form-wide">Notes<textarea value={form.notes} onChange={(event) => { setForm({ ...form, notes: event.target.value }); }} /></label>
    <p className="form-wide privacy-note">Timezone: {timezone}. The entered unit is preserved; kilograms use 1 lb = 0.45359237 kg.</p>
    {mutation.isSuccess ? <p className="success-message form-wide" role="status">Weight recorded.</p> : null}
    {mutation.isError ? <p className="error-summary form-wide" role="alert">Weight was not saved. Check the value and time.</p> : null}
    <button className="form-wide" type="submit" disabled={mutation.isPending}>{mutation.isPending ? "Saving…" : "Record weight"}</button>
  </form>;
}

function BloodPressureCorrection({ record, close }: { record: BloodPressure; close: () => void }): React.JSX.Element {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ systolic: record.systolic_mmhg.toString(), diastolic: record.diastolic_mmhg.toString(), pulse: record.pulse_bpm?.toString() ?? "", localTime: record.time.local_time.slice(0, 16), timezone: record.time.timezone, notes: record.notes ?? "", reason: "" });
  const [validation, setValidation] = useState<string | null>(null);
  const mutation = useMutation({ mutationFn: (payload: BloodPressureCorrectionInput) => correctBloodPressure(record.id, payload), onSuccess: async () => { await Promise.all([queryClient.invalidateQueries({ queryKey: ["blood-pressure"] }), queryClient.invalidateQueries({ queryKey: ["timeline"] })]); close(); } });
  function submit(event: React.SyntheticEvent<HTMLFormElement>): void {
    event.preventDefault();
    const changes: BloodPressureCorrectionInput["changes"] = {};
    if (Number(form.systolic) !== record.systolic_mmhg) changes.systolic_mmhg = Number(form.systolic);
    if (Number(form.diastolic) !== record.diastolic_mmhg) changes.diastolic_mmhg = Number(form.diastolic);
    if (form.pulse !== (record.pulse_bpm?.toString() ?? "")) changes.pulse_bpm = form.pulse === "" ? null : Number(form.pulse);
    if (form.localTime !== record.time.local_time.slice(0, 16) || form.timezone !== record.time.timezone) changes.time = { local_time: form.localTime, timezone: form.timezone };
    if (form.notes !== (record.notes ?? "")) changes.notes = form.notes === "" ? null : form.notes;
    if (form.reason.trim() === "" || Object.keys(changes).length === 0) { setValidation(form.reason.trim() === "" ? "Explain why this fact needs correction." : "Change at least one recorded field."); return; }
    setValidation(null); mutation.mutate({ reason: form.reason.trim(), changes });
  }
  return <form className="correction-form" aria-label="Correct blood pressure" onSubmit={submit}><p className="correction-warning">This creates a corrected fact and preserves the original.</p><label>Systolic (mmHg)<input required type="number" min="1" max="500" value={form.systolic} onChange={(event) => { setForm({ ...form, systolic: event.target.value }); }} /></label><label>Diastolic (mmHg)<input required type="number" min="1" max="500" value={form.diastolic} onChange={(event) => { setForm({ ...form, diastolic: event.target.value }); }} /></label><label>Pulse (bpm)<input type="number" min="1" max="500" value={form.pulse} onChange={(event) => { setForm({ ...form, pulse: event.target.value }); }} /></label><label>Experienced local time<input required type="datetime-local" value={form.localTime} onChange={(event) => { setForm({ ...form, localTime: event.target.value }); }} /></label><label>Timezone<input required value={form.timezone} onChange={(event) => { setForm({ ...form, timezone: event.target.value }); }} /></label><label className="form-wide">Notes<textarea value={form.notes} onChange={(event) => { setForm({ ...form, notes: event.target.value }); }} /></label><label className="form-wide">Correction reason<input required value={form.reason} onChange={(event) => { setForm({ ...form, reason: event.target.value }); }} /></label>{validation === null ? null : <p className="error-summary form-wide" role="alert">{validation}</p>}{mutation.isError ? <p className="error-summary form-wide" role="alert">The correction was not saved.</p> : null}<div className="filter-actions form-wide"><button type="submit" disabled={mutation.isPending}>Save corrected fact</button><button className="button-secondary" type="button" onClick={close}>Cancel</button></div></form>;
}

function WeightCorrection({ record, close }: { record: Weight; close: () => void }): React.JSX.Element {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ value: record.value, unit: record.unit, localTime: record.time.local_time.slice(0, 16), timezone: record.time.timezone, notes: record.notes ?? "", reason: "" });
  const [validation, setValidation] = useState<string | null>(null);
  const mutation = useMutation({ mutationFn: (payload: WeightCorrectionInput) => correctWeight(record.id, payload), onSuccess: async () => { await Promise.all([queryClient.invalidateQueries({ queryKey: ["weight"] }), queryClient.invalidateQueries({ queryKey: ["timeline"] })]); close(); } });
  function submit(event: React.SyntheticEvent<HTMLFormElement>): void {
    event.preventDefault(); const changes: WeightCorrectionInput["changes"] = {};
    if (form.value !== record.value) changes.value = form.value;
    if (form.unit !== record.unit) changes.unit = form.unit;
    if (form.localTime !== record.time.local_time.slice(0, 16) || form.timezone !== record.time.timezone) changes.time = { local_time: form.localTime, timezone: form.timezone };
    if (form.notes !== (record.notes ?? "")) changes.notes = form.notes === "" ? null : form.notes;
    if (form.reason.trim() === "" || Object.keys(changes).length === 0) { setValidation(form.reason.trim() === "" ? "Explain why this fact needs correction." : "Change at least one recorded field."); return; }
    setValidation(null); mutation.mutate({ reason: form.reason.trim(), changes });
  }
  return <form className="correction-form" aria-label="Correct weight" onSubmit={submit}><p className="correction-warning">This creates a corrected fact and preserves the original.</p><label>Value<input required type="number" min="0.0001" max="5000" step="any" value={form.value} onChange={(event) => { setForm({ ...form, value: event.target.value }); }} /></label><label>Unit<select value={form.unit} onChange={(event) => { setForm({ ...form, unit: event.target.value as Weight["unit"] }); }}><option value="lb">lb</option><option value="kg">kg</option></select></label><label>Experienced local time<input required type="datetime-local" value={form.localTime} onChange={(event) => { setForm({ ...form, localTime: event.target.value }); }} /></label><label>Timezone<input required value={form.timezone} onChange={(event) => { setForm({ ...form, timezone: event.target.value }); }} /></label><label className="form-wide">Notes<textarea value={form.notes} onChange={(event) => { setForm({ ...form, notes: event.target.value }); }} /></label><label className="form-wide">Correction reason<input required value={form.reason} onChange={(event) => { setForm({ ...form, reason: event.target.value }); }} /></label>{validation === null ? null : <p className="error-summary form-wide" role="alert">{validation}</p>}{mutation.isError ? <p className="error-summary form-wide" role="alert">The correction was not saved.</p> : null}<div className="filter-actions form-wide"><button type="submit" disabled={mutation.isPending}>Save corrected fact</button><button className="button-secondary" type="button" onClick={close}>Cancel</button></div></form>;
}

export function HealthDataPage(): React.JSX.Element {
  const { session } = useAuth();
  const timezone = session?.user.defaultTimezone ?? "UTC";
  const [editing, setEditing] = useState<string | null>(null);
  const [bpPage, setBpPage] = useState(1);
  const [weightPage, setWeightPage] = useState(1);
  const [garminPage, setGarminPage] = useState(1);
  const bp = useQuery({ queryKey: ["blood-pressure", bpPage], queryFn: () => getBloodPressure(bpPage) });
  const weight = useQuery({ queryKey: ["weight", weightPage], queryFn: () => getWeight(weightPage) });
  const garmin = useQuery({ queryKey: ["garmin-records", garminPage], queryFn: () => getGarminRecords(garminPage) });
  const currentBp = bp.data?.items ?? [];
  const currentWeight = weight.data?.items ?? [];
  const bpById = new Map([...currentBp, ...(bp.data?.revisions ?? [])].map((record) => [record.id, record]));
  const weightById = new Map([...currentWeight, ...(weight.data?.revisions ?? [])].map((record) => [record.id, record]));
  return <Page title="Health data" description="Record and review blood pressure, weight, and Garmin observations as measured facts. HealthCurve does not diagnose or recommend treatment from these values.">
    <section aria-labelledby="quick-entry-heading"><h2 id="quick-entry-heading">Quick entry</h2><div className="vital-entry-grid"><BloodPressureEntry timezone={timezone} /><WeightEntry timezone={timezone} /></div></section>
    {bp.isPending || weight.isPending || garmin.isPending ? <p role="status">Loading recorded health data…</p> : null}
    {bp.isError || weight.isError || garmin.isError ? <p className="error-summary" role="alert">Some health records could not be loaded.</p> : null}
    <section aria-labelledby="trends-heading"><h2 id="trends-heading">Recorded trends</h2><p className="privacy-note">Charts connect recorded observations only. They do not infer readings between observations; absence of a record is not a zero.</p>
      {currentBp.length === 0 ? <p>No blood-pressure readings recorded.</p> : <AccessibleLineChart title="Blood pressure" summary="Systolic and diastolic measurements on the visible records page." unit="mmHg" timezone={timezone} dateRange={dateRange(currentBp)} definition="Each point is one current blood-pressure fact on the visible records page. Missing intervals are not inferred; the table contains every plotted reading." sampleCount={currentBp.length} missingCount={0} xAxisLabel="Experienced date / time" yAxisLabel="Blood pressure" series={bpSeries(currentBp)} />}
      {currentWeight.length === 0 ? <p>No weight readings recorded.</p> : <AccessibleLineChart title="Weight" summary="Weight measurements on the visible records page, shown on one consistent pounds scale." unit="lb" timezone={timezone} dateRange={dateRange(currentWeight)} definition="Each point is one current weight fact on the visible records page, converted deterministically to pounds and rounded half up to 0.1 lb using 1 lb = 0.45359237 kg. The chart adds one pound of visual padding above and below the observed range; exact values remain in the chart points and records table. Missing intervals are not inferred." sampleCount={currentWeight.length} missingCount={0} xAxisLabel="Experienced date / time" yAxisLabel="Weight" yPadding={1} compactPlot series={weightSeries(currentWeight)} />}
    </section>
    <section aria-labelledby="garmin-records-heading"><h2 id="garmin-records-heading">Garmin recorded observations</h2><p className="privacy-note">These are provider-imported facts, separate from physician-approved plans and AI analysis. Scores and missing values are not interpreted as medical conclusions.</p>{garmin.data?.records.length === 0 ? <p>No Garmin observations recorded.</p> : garmin.data === undefined ? null : <GarminRecordsTable records={garmin.data.records} />}{garmin.data === undefined ? null : <PaginationControls label="Garmin records" metadata={garmin.data.page} onPageChange={setGarminPage} />}</section>
    <section aria-labelledby="bp-records-heading"><h2 id="bp-records-heading">Blood pressure records and provenance</h2>{currentBp.length === 0 ? <p>No blood-pressure readings recorded.</p> : <BloodPressureHistoryTable records={currentBp} byId={bpById} editing={editing} setEditing={setEditing} />}{bp.data === undefined ? null : <PaginationControls label="Blood pressure records" metadata={bp.data.page} onPageChange={(nextPage) => { setEditing(null); setBpPage(nextPage); }} />}</section>
    <section aria-labelledby="weight-records-heading"><h2 id="weight-records-heading">Weight records and provenance</h2>{currentWeight.length === 0 ? <p>No weight readings recorded.</p> : <WeightHistoryTable records={currentWeight} byId={weightById} editing={editing} setEditing={setEditing} />}{weight.data === undefined ? null : <PaginationControls label="Weight records" metadata={weight.data.page} onPageChange={(nextPage) => { setEditing(null); setWeightPage(nextPage); }} />}</section>
  </Page>;
}

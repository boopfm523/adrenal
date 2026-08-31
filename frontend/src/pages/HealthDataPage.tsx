import { Alert, Button, Group, NativeSelect, Paper, SimpleGrid, Text, TextInput, Textarea, Title } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import {
  correctBloodPressure,
  correctTemperature,
  correctWeight,
  createBloodPressure,
  createTemperature,
  createWeight,
  getBloodPressure,
  getGarminRecords,
  getTemperature,
  getWeight,
  type BloodPressure,
  type BloodPressureCorrectionInput,
  type BloodPressureInput,
  type GarminRecord,
  type HealthDataFilters,
  type Temperature,
  type TemperatureCorrectionInput,
  type TemperatureInput,
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
  formatDistanceMiles,
  formatGarminDailyValue,
  garminMetricLabel,
  humanizeSource,
} from "../format";
import { timezoneAbbreviation } from "../time";

function localNow(): string {
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60_000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 16);
}

function displayTime(value: string): string {
  return value.replace("T", " ").slice(0, 16);
}

function CompactLocalDateTime({ value, onChange }: { value: string; onChange: (value: string) => void }): React.JSX.Element {
  const [date = "", time = ""] = value.split("T");
  return <fieldset className="compact-local-time form-wide">
    <legend>Experienced local time</legend>
    <TextInput label="Date" required aria-label="Date" type="date" value={date} onChange={(event) => { onChange(`${event.target.value}T${time || "00:00"}`); }} />
    <TextInput label="Time" required aria-label="Time" type="time" value={time.slice(0, 5)} onChange={(event) => { onChange(`${date}T${event.target.value}`); }} />
  </fieldset>;
}

interface HealthDataViewState extends HealthDataFilters {
  bpPage: number;
  weightPage: number;
  temperaturePage: number;
  garminPage: number;
}

function pageFromSearch(params: URLSearchParams, name: string): number {
  const value = params.get(name) ?? "";
  return /^\d+$/.test(value) && Number(value) >= 1 ? Number(value) : 1;
}

function viewStateFromSearch(search: string, profileTimezone: string): HealthDataViewState {
  const params = new URLSearchParams(search);
  return {
    dateFrom: params.get("local_date_from") ?? "",
    dateTo: params.get("local_date_to") ?? "",
    timezone: params.get("timezone") ?? profileTimezone,
    bpPage: pageFromSearch(params, "bp_page"),
    weightPage: pageFromSearch(params, "weight_page"),
    temperaturePage: pageFromSearch(params, "temperature_page"),
    garminPage: pageFromSearch(params, "garmin_page"),
  };
}

function searchFromViewState(state: HealthDataViewState): URLSearchParams {
  const params = new URLSearchParams({ timezone: state.timezone });
  if (state.dateFrom !== "") params.set("local_date_from", state.dateFrom);
  if (state.dateTo !== "") params.set("local_date_to", state.dateTo);
  if (state.bpPage > 1) params.set("bp_page", state.bpPage.toString());
  if (state.weightPage > 1) params.set("weight_page", state.weightPage.toString());
  if (state.temperaturePage > 1) params.set("temperature_page", state.temperaturePage.toString());
  if (state.garminPage > 1) params.set("garmin_page", state.garminPage.toString());
  return params;
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
        <td className="timeline-time">{displayTime(record.time.local_time)}<span>{timezoneAbbreviation(record.time.timezone, record.time.occurred_at)}</span></td>
        <td><strong>{record.kind === "daily" ? record.measurement_label ?? garminMetricLabel(record.metric_type) : record.kind === "sleep" ? "Sleep" : humanizeSource(record.activity_type ?? "Activity")}</strong>{record.provenance.is_correction ? <span>{`Provider correction · ${record.provenance.correction_reason ?? "reason recorded"}`}</span> : null}</td>
        <td>{record.kind === "daily" ? formatGarminDailyValue(record.metric_type, record.value, record.unit) : record.kind === "sleep" ? record.sleep_kind === "nap" ? <>Garmin nap interval</> : <>Sleep score: {record.sleep_score ?? <span className="missing-value">Unavailable</span>}</> : <>Distance: {record.distance_miles == null ? <span className="missing-value">Unavailable</span> : `${formatDistanceMiles(record.distance_miles)} mi`}</>}</td>
        <td>{record.kind === "daily" ? <span className="missing-value">Untimed aggregate{record.period_label === null || record.period_label === undefined ? "" : ` · ${record.period_label}`}</span> : <>{duration(record.duration_seconds)}{record.kind === "sleep" ? <><span>Awakenings: {record.awakenings ?? "Unavailable"}</span><span>Duration source: {record.duration_source?.replaceAll("_", " ") ?? "Unavailable"}</span></> : null}</>}</td>
      </tr>)}</tbody>
    </table>
  </div>;
}

function dateRange(records: (BloodPressure | Weight | Temperature)[]): string {
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

function measurementSetting(value: "home" | "provider"): string {
  return value === "provider" ? "Provider / clinic" : "Home";
}

const bodyPositionOptions = [
  { value: "", label: "Not recorded" },
  { value: "lying", label: "Lying" },
  { value: "sitting", label: "Sitting" },
  { value: "standing", label: "Standing" },
];

function bodyPosition(value: BloodPressure["body_position"] | undefined): string {
  return value == null ? "Not recorded" : value.charAt(0).toUpperCase() + value.slice(1);
}

function temperatureSeries(records: Temperature[]): ChartSeries[] {
  const ordered = [...records].sort((left, right) => left.time.occurred_at.localeCompare(right.time.occurred_at));
  return [{
    name: "Temperature",
    source: "current recorded facts",
    values: ordered.map((row) => ({ label: displayTime(row.time.local_time), value: row.display_f })),
  }];
}

function WeightHistoryTable({ records, byId, editing, setEditing }: {
  records: Weight[];
  byId: Map<string, Weight>;
  editing: string | null;
  setEditing: (id: string | null) => void;
}): React.JSX.Element {
  return <div className="table-scroll vital-table-region" tabIndex={0} role="region" aria-label="Weight records table">
    <table className="vital-table weight-table">
      <caption>Current recorded weight facts on a consistent pounds scale, with normalized kilograms beneath each value.</caption>
      <thead><tr><th scope="col">Experienced time</th><th scope="col">Weight</th><th scope="col">Setting</th><th scope="col">Source</th><th scope="col">Notes</th><th scope="col">Action</th></tr></thead>
      <tbody>{records.map((record) => {
        const history = historyFor(record, byId);
        return <Fragment key={record.id}>
          <tr data-category="fact">
            <td className="timeline-time">{displayTime(record.time.local_time)}<span>{timezoneAbbreviation(record.time.timezone, record.time.occurred_at)}</span></td>
            <td className="weight-primary"><strong>{formatDecimal(record.display_lb)} lb</strong><span className="secondary-measurement">{formatDecimal(record.normalized_kg)} kg</span></td>
            <td>{measurementSetting(record.measurement_setting)}</td>
            <td>{humanizeSource(record.provenance.source_type)}</td>
            <td>{record.notes ?? <span className="missing-value">None</span>}</td>
            <td>{record.provenance.is_correction ? <span>{`Corrected · ${record.provenance.correction_reason ?? "reason recorded"}`}</span> : null}<Button mt="sm" type="button" onClick={() => { setEditing(editing === record.id ? null : record.id); }}>{editing === record.id ? "Close correction form" : "Correct weight"}</Button>{history.length === 0 ? null : <details className="revision-history"><summary>Revision history ({history.length})</summary>{history.map((prior) => <article key={prior.id}><h3>Superseded value</h3><p><strong>{formatDecimal(prior.display_lb)} lb</strong> · entered {formatDecimal(prior.value)} {prior.unit} · {measurementSetting(prior.measurement_setting)}</p><p>{displayTime(prior.time.local_time)} · {timezoneAbbreviation(prior.time.timezone, prior.time.occurred_at)}</p><p>Source: {source(prior)}</p></article>)}</details>}</td>
          </tr>
          {editing === record.id ? <tr className="correction-table-row"><td colSpan={6}><WeightCorrection record={record} close={() => { setEditing(null); }} /></td></tr> : null}
        </Fragment>;
      })}</tbody>
    </table>
  </div>;
}

function TemperatureHistoryTable({ records, byId, editing, setEditing }: {
  records: Temperature[];
  byId: Map<string, Temperature>;
  editing: string | null;
  setEditing: (id: string | null) => void;
}): React.JSX.Element {
  const ordered = [...records].sort((left, right) => right.time.occurred_at.localeCompare(left.time.occurred_at));
  return <div className="table-scroll vital-table-region" tabIndex={0} role="region" aria-label="Temperature records table">
    <table className="vital-table temperature-table">
      <caption>Current recorded body-temperature facts, shown Fahrenheit first with Celsius in parentheses. Original values and correction history remain preserved.</caption>
      <thead><tr><th scope="col">Experienced time</th><th scope="col">Temperature</th><th scope="col">Entered value</th><th scope="col">Source</th><th scope="col">Notes</th><th scope="col">Action</th></tr></thead>
      <tbody>{ordered.map((record) => {
        const history = historyFor(record, byId);
        return <Fragment key={record.id}>
          <tr data-category="fact">
            <td className="timeline-time">{displayTime(record.time.local_time)}<span>{timezoneAbbreviation(record.time.timezone, record.time.occurred_at)}</span></td>
            <th scope="row">{record.display_f} °F ({record.display_c} °C)</th>
            <td>{formatDecimal(record.value)} °{record.unit.toUpperCase()}</td>
            <td>{humanizeSource(record.provenance.source_type)}</td>
            <td>{record.notes ?? <span className="missing-value">None</span>}</td>
            <td>{record.provenance.is_correction ? <span>{`Corrected · ${record.provenance.correction_reason ?? "reason recorded"}`}</span> : <span>Original record</span>}<Button mt="sm" type="button" onClick={() => { setEditing(editing === record.id ? null : record.id); }}>{editing === record.id ? "Close correction form" : "Correct temperature"}</Button>{history.length === 0 ? null : <details className="revision-history"><summary>Revision history ({history.length})</summary>{history.map((prior) => <article key={prior.id}><h3>Superseded value</h3><p><strong>{prior.display_f} °F ({prior.display_c} °C)</strong> · entered {formatDecimal(prior.value)} °{prior.unit.toUpperCase()}</p><p>{displayTime(prior.time.local_time)} · {timezoneAbbreviation(prior.time.timezone, prior.time.occurred_at)}</p><p>Source: {source(prior)}</p></article>)}</details>}</td>
          </tr>
          {editing === record.id ? <tr className="correction-table-row"><td colSpan={6}><TemperatureCorrection record={record} close={() => { setEditing(null); }} /></td></tr> : null}
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
      <thead><tr><th scope="col">Experienced time</th><th scope="col">Systolic / diastolic</th><th scope="col">Pulse</th><th scope="col">Setting and position</th><th scope="col">Source and confirmation</th><th scope="col">Notes</th><th scope="col">Action</th></tr></thead>
      <tbody>{ordered.map((record) => {
        const history = historyFor(record, byId);
        return <Fragment key={record.id}>
          <tr data-category="fact">
            <td className="timeline-time">{displayTime(record.time.local_time)}<span>{timezoneAbbreviation(record.time.timezone, record.time.occurred_at)}</span></td>
            <th scope="row" className="blood-pressure-primary">{record.systolic_mmhg.toString()}/{record.diastolic_mmhg.toString()} mmHg</th>
            <td>{record.pulse_bpm === null ? <span className="missing-value">Not recorded</span> : `${record.pulse_bpm.toString()} bpm`}</td>
            <td>{measurementSetting(record.measurement_setting)}<span>{`Position: ${bodyPosition(record.body_position)}`}</span></td>
            <td><span>{humanizeSource(record.provenance.source_type)}</span><span>{humanizeSource(record.provenance.confirmation_state)}</span></td>
            <td>{record.notes ?? <span className="missing-value">None</span>}</td>
            <td>{record.provenance.is_correction ? <span>{`Corrected · ${record.provenance.correction_reason ?? "reason recorded"}`}</span> : <span>Original record</span>}<Button mt="sm" type="button" onClick={() => { setEditing(editing === record.id ? null : record.id); }}>{editing === record.id ? "Close correction form" : "Correct blood pressure"}</Button>{history.length === 0 ? null : <details className="revision-history"><summary>Revision history ({history.length})</summary>{history.map((prior) => <article key={prior.id}><h3>Superseded value</h3><p><strong>{prior.systolic_mmhg}/{prior.diastolic_mmhg} mmHg</strong>{prior.pulse_bpm === null ? " · pulse not recorded" : ` · pulse ${prior.pulse_bpm.toString()} bpm`} · {measurementSetting(prior.measurement_setting)} · position {bodyPosition(prior.body_position)}</p><p>{displayTime(prior.time.local_time)} · {timezoneAbbreviation(prior.time.timezone, prior.time.occurred_at)}</p><p>Source: {source(prior)}</p>{prior.notes === null ? null : <p>Notes: {prior.notes}</p>}{prior.provenance.is_correction ? <p>{`Corrected · ${prior.provenance.correction_reason ?? "reason recorded"}`}</p> : <p>Original record</p>}</article>)}</details>}</td>
          </tr>
          {editing === record.id ? <tr className="correction-table-row"><td colSpan={7}><BloodPressureCorrection record={record} close={() => { setEditing(null); }} /></td></tr> : null}
        </Fragment>;
      })}</tbody>
    </table>
  </div>;
}

function BloodPressureEntry({ timezone }: { timezone: string }): React.JSX.Element {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ systolic: "", diastolic: "", pulse: "", measurementSetting: "home" as BloodPressureInput["measurement_setting"], bodyPosition: "", localTime: localNow(), notes: "" });
  const mutation = useMutation({
    mutationFn: createBloodPressure,
    onSuccess: async () => {
      setForm({ systolic: "", diastolic: "", pulse: "", measurementSetting: form.measurementSetting, bodyPosition: form.bodyPosition, localTime: localNow(), notes: "" });
      await Promise.all([queryClient.invalidateQueries({ queryKey: ["blood-pressure"] }), queryClient.invalidateQueries({ queryKey: ["timeline"] })]);
    },
  });
  function submit(event: React.SyntheticEvent<HTMLFormElement>): void {
    event.preventDefault();
    const payload: BloodPressureInput = {
      systolic_mmhg: Number(form.systolic),
      diastolic_mmhg: Number(form.diastolic),
      pulse_bpm: form.pulse === "" ? null : Number(form.pulse),
      measurement_setting: form.measurementSetting,
      body_position: form.bodyPosition === "" ? null : form.bodyPosition as NonNullable<BloodPressureInput["body_position"]>,
      time: { local_time: form.localTime, timezone },
      notes: form.notes === "" ? null : form.notes,
    };
    mutation.mutate(payload);
  }
  return <form aria-label="Record blood pressure" onSubmit={submit}><Paper className="vital-entry-form" withBorder p="lg" radius="lg">
    <Title order={3}>Blood pressure</Title>
    <div className="measurement-row measurement-row--equal"><TextInput label="Systolic (mmHg)" required aria-label="Systolic (mmHg)" type="number" inputMode="numeric" min="1" max="500" value={form.systolic} onChange={(event) => { setForm({ ...form, systolic: event.target.value }); }} />
    <TextInput label="Diastolic (mmHg)" required aria-label="Diastolic (mmHg)" type="number" inputMode="numeric" min="1" max="500" value={form.diastolic} onChange={(event) => { setForm({ ...form, diastolic: event.target.value }); }} /></div>
    <div className="vital-entry-secondary measurement-row--setting"><TextInput label="Pulse (bpm, optional)" type="number" inputMode="numeric" min="1" max="500" value={form.pulse} onChange={(event) => { setForm({ ...form, pulse: event.target.value }); }} />
    <NativeSelect label="Measurement setting" value={form.measurementSetting} onChange={(event) => { setForm({ ...form, measurementSetting: event.target.value as BloodPressureInput["measurement_setting"] }); }} data={[{value:"home",label:"Home"},{value:"provider",label:"Provider / clinic"}]} /></div>
    <NativeSelect label="Body position" description="Optional posture at the time of this separate blood-pressure reading." value={form.bodyPosition} onChange={(event) => { setForm({ ...form, bodyPosition: event.target.value }); }} data={bodyPositionOptions} />
    <CompactLocalDateTime value={form.localTime} onChange={(localTime) => { setForm({ ...form, localTime }); }} />
    <Textarea className="form-wide" label="Notes" value={form.notes} onChange={(event) => { setForm({ ...form, notes: event.target.value }); }} />
    <Text className="form-wide" c="dimmed">Timezone: {timezoneAbbreviation(timezone)}. HealthCurve records the values without interpreting them.</Text>
    {mutation.isSuccess ? <Alert className="form-wide" color="green" role="status">Blood pressure recorded.</Alert> : null}
    {mutation.isError ? <Alert className="form-wide" color="red" role="alert">Blood pressure was not saved. Check the values and time.</Alert> : null}
    <Button className="form-wide" type="submit" loading={mutation.isPending}>Record blood pressure</Button>
  </Paper></form>;
}

function WeightEntry({ timezone }: { timezone: string }): React.JSX.Element {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ value: "", unit: "lb" as WeightInput["unit"], measurementSetting: "home" as WeightInput["measurement_setting"], localTime: localNow(), notes: "" });
  const mutation = useMutation({
    mutationFn: createWeight,
    onSuccess: async () => {
      setForm({ value: "", unit: form.unit, measurementSetting: form.measurementSetting, localTime: localNow(), notes: "" });
      await Promise.all([queryClient.invalidateQueries({ queryKey: ["weight"] }), queryClient.invalidateQueries({ queryKey: ["timeline"] })]);
    },
  });
  function submit(event: React.SyntheticEvent<HTMLFormElement>): void {
    event.preventDefault();
    mutation.mutate({ value: form.value, unit: form.unit, measurement_setting: form.measurementSetting, time: { local_time: form.localTime, timezone }, notes: form.notes === "" ? null : form.notes });
  }
  return <form aria-label="Record weight" onSubmit={submit}><Paper className="vital-entry-form" withBorder p="lg" radius="lg">
    <Title order={3}>Weight</Title>
    <div className="measurement-row"><TextInput label="Value" required aria-label="Value" type="number" inputMode="decimal" min="0.0001" max="5000" step="any" value={form.value} onChange={(event) => { setForm({ ...form, value: event.target.value }); }} />
    <NativeSelect className="unit-select" label="Unit" value={form.unit} onChange={(event) => { setForm({ ...form, unit: event.target.value as WeightInput["unit"] }); }} data={["lb","kg"]} /></div>
    <div className="vital-entry-secondary measurement-row--setting"><NativeSelect label="Measurement setting" value={form.measurementSetting} onChange={(event) => { setForm({ ...form, measurementSetting: event.target.value as WeightInput["measurement_setting"] }); }} data={[{value:"home",label:"Home"},{value:"provider",label:"Provider / clinic"}]} /></div>
    <CompactLocalDateTime value={form.localTime} onChange={(localTime) => { setForm({ ...form, localTime }); }} />
    <Textarea className="form-wide" label="Notes" value={form.notes} onChange={(event) => { setForm({ ...form, notes: event.target.value }); }} />
    <Text className="form-wide" c="dimmed">Timezone: {timezoneAbbreviation(timezone)}. The entered unit is preserved; kilograms use 1 lb = 0.45359237 kg.</Text>
    {mutation.isSuccess ? <Alert className="form-wide" color="green" role="status">Weight recorded.</Alert> : null}
    {mutation.isError ? <Alert className="form-wide" color="red" role="alert">Weight was not saved. Check the value and time.</Alert> : null}
    <Button className="form-wide" type="submit" loading={mutation.isPending}>Record weight</Button>
  </Paper></form>;
}

function TemperatureEntry({ timezone }: { timezone: string }): React.JSX.Element {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ value: "", unit: "f" as TemperatureInput["unit"], localTime: localNow(), notes: "" });
  const mutation = useMutation({
    mutationFn: createTemperature,
    onSuccess: async () => {
      setForm({ value: "", unit: form.unit, localTime: localNow(), notes: "" });
      await Promise.all([queryClient.invalidateQueries({ queryKey: ["temperature"] }), queryClient.invalidateQueries({ queryKey: ["timeline"] })]);
    },
  });
  function submit(event: React.SyntheticEvent<HTMLFormElement>): void {
    event.preventDefault();
    mutation.mutate({ value: form.value, unit: form.unit, time: { local_time: form.localTime, timezone }, notes: form.notes === "" ? null : form.notes });
  }
  const bounds = form.unit === "f" ? { min: 77, max: 113 } : { min: 25, max: 45 };
  return <form aria-label="Record body temperature" onSubmit={submit}><Paper className="vital-entry-form" withBorder p="lg" radius="lg">
    <Title order={3}>Body temperature</Title>
    <div className="measurement-row"><TextInput label="Value" required aria-label="Value" type="number" inputMode="decimal" min={bounds.min} max={bounds.max} step="0.1" value={form.value} onChange={(event) => { setForm({ ...form, value: event.target.value }); }} />
    <NativeSelect className="unit-select" label="Unit" value={form.unit} onChange={(event) => { setForm({ ...form, unit: event.target.value as TemperatureInput["unit"], value: "" }); }} data={[{value:"f",label:"°F"},{value:"c",label:"°C"}]} /></div>
    <div className="vital-entry-secondary vital-entry-secondary--empty" aria-hidden="true" />
    <CompactLocalDateTime value={form.localTime} onChange={(localTime) => { setForm({ ...form, localTime }); }} />
    <Textarea className="form-wide" label="Notes" value={form.notes} onChange={(event) => { setForm({ ...form, notes: event.target.value }); }} />
    <Text className="form-wide" c="dimmed">Timezone: {timezoneAbbreviation(timezone)}. Entered units are preserved. HealthCurve converts with °F = (°C × 9/5) + 32 and does not diagnose fever.</Text>
    {mutation.isSuccess ? <Alert className="form-wide" color="green" role="status">Temperature recorded.</Alert> : null}
    {mutation.isError ? <Alert className="form-wide" color="red" role="alert">Temperature was not saved. Use 77–113 °F or 25–45 °C and check the time.</Alert> : null}
    <Button className="form-wide" type="submit" loading={mutation.isPending}>Record temperature</Button>
  </Paper></form>;
}

function BloodPressureCorrection({ record, close }: { record: BloodPressure; close: () => void }): React.JSX.Element {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ systolic: record.systolic_mmhg.toString(), diastolic: record.diastolic_mmhg.toString(), pulse: record.pulse_bpm?.toString() ?? "", measurementSetting: record.measurement_setting, bodyPosition: record.body_position ?? "", localTime: record.time.local_time.slice(0, 16), timezone: record.time.timezone, notes: record.notes ?? "", reason: "" });
  const [validation, setValidation] = useState<string | null>(null);
  const mutation = useMutation({ mutationFn: (payload: BloodPressureCorrectionInput) => correctBloodPressure(record.id, payload), onSuccess: async () => { await Promise.all([queryClient.invalidateQueries({ queryKey: ["blood-pressure"] }), queryClient.invalidateQueries({ queryKey: ["timeline"] })]); close(); } });
  function submit(event: React.SyntheticEvent<HTMLFormElement>): void {
    event.preventDefault();
    const changes: BloodPressureCorrectionInput["changes"] = {};
    if (Number(form.systolic) !== record.systolic_mmhg) changes.systolic_mmhg = Number(form.systolic);
    if (Number(form.diastolic) !== record.diastolic_mmhg) changes.diastolic_mmhg = Number(form.diastolic);
    if (form.pulse !== (record.pulse_bpm?.toString() ?? "")) changes.pulse_bpm = form.pulse === "" ? null : Number(form.pulse);
    if (form.measurementSetting !== record.measurement_setting) changes.measurement_setting = form.measurementSetting;
    if (form.bodyPosition !== (record.body_position ?? "")) changes.body_position = form.bodyPosition === "" ? null : form.bodyPosition as NonNullable<BloodPressure["body_position"]>;
    if (form.localTime !== record.time.local_time.slice(0, 16) || form.timezone !== record.time.timezone) changes.time = { local_time: form.localTime, timezone: form.timezone };
    if (form.notes !== (record.notes ?? "")) changes.notes = form.notes === "" ? null : form.notes;
    if (form.reason.trim() === "" || Object.keys(changes).length === 0) { setValidation(form.reason.trim() === "" ? "Explain why this fact needs correction." : "Change at least one recorded field."); return; }
    setValidation(null); mutation.mutate({ reason: form.reason.trim(), changes });
  }
  return <form className="correction-form" aria-label="Correct blood pressure" onSubmit={submit}><p className="correction-warning">This creates a corrected fact and preserves the original.</p><label>Systolic (mmHg)<input required type="number" min="1" max="500" value={form.systolic} onChange={(event) => { setForm({ ...form, systolic: event.target.value }); }} /></label><label>Diastolic (mmHg)<input required type="number" min="1" max="500" value={form.diastolic} onChange={(event) => { setForm({ ...form, diastolic: event.target.value }); }} /></label><label>Pulse (bpm)<input type="number" min="1" max="500" value={form.pulse} onChange={(event) => { setForm({ ...form, pulse: event.target.value }); }} /></label><label>Measurement setting<select value={form.measurementSetting} onChange={(event) => { setForm({ ...form, measurementSetting: event.target.value as BloodPressure["measurement_setting"] }); }}><option value="home">Home</option><option value="provider">Provider / clinic</option></select></label><label>Body position<select value={form.bodyPosition} onChange={(event) => { setForm({ ...form, bodyPosition: event.target.value }); }}>{bodyPositionOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label><label>Experienced local time<input required type="datetime-local" value={form.localTime} onChange={(event) => { setForm({ ...form, localTime: event.target.value }); }} /></label><label>Timezone<input required value={form.timezone} onChange={(event) => { setForm({ ...form, timezone: event.target.value }); }} /></label><label className="form-wide">Notes<textarea value={form.notes} onChange={(event) => { setForm({ ...form, notes: event.target.value }); }} /></label><label className="form-wide">Correction reason<input required value={form.reason} onChange={(event) => { setForm({ ...form, reason: event.target.value }); }} /></label>{validation === null ? null : <p className="error-summary form-wide" role="alert">{validation}</p>}{mutation.isError ? <p className="error-summary form-wide" role="alert">The correction was not saved.</p> : null}<div className="filter-actions form-wide"><button type="submit" disabled={mutation.isPending}>Save corrected fact</button><button className="button-secondary" type="button" onClick={close}>Cancel</button></div></form>;
}

function WeightCorrection({ record, close }: { record: Weight; close: () => void }): React.JSX.Element {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ value: record.value, unit: record.unit, measurementSetting: record.measurement_setting, localTime: record.time.local_time.slice(0, 16), timezone: record.time.timezone, notes: record.notes ?? "", reason: "" });
  const [validation, setValidation] = useState<string | null>(null);
  const mutation = useMutation({ mutationFn: (payload: WeightCorrectionInput) => correctWeight(record.id, payload), onSuccess: async () => { await Promise.all([queryClient.invalidateQueries({ queryKey: ["weight"] }), queryClient.invalidateQueries({ queryKey: ["timeline"] })]); close(); } });
  function submit(event: React.SyntheticEvent<HTMLFormElement>): void {
    event.preventDefault(); const changes: WeightCorrectionInput["changes"] = {};
    if (form.value !== record.value) changes.value = form.value;
    if (form.unit !== record.unit) changes.unit = form.unit;
    if (form.measurementSetting !== record.measurement_setting) changes.measurement_setting = form.measurementSetting;
    if (form.localTime !== record.time.local_time.slice(0, 16) || form.timezone !== record.time.timezone) changes.time = { local_time: form.localTime, timezone: form.timezone };
    if (form.notes !== (record.notes ?? "")) changes.notes = form.notes === "" ? null : form.notes;
    if (form.reason.trim() === "" || Object.keys(changes).length === 0) { setValidation(form.reason.trim() === "" ? "Explain why this fact needs correction." : "Change at least one recorded field."); return; }
    setValidation(null); mutation.mutate({ reason: form.reason.trim(), changes });
  }
  return <form className="correction-form" aria-label="Correct weight" onSubmit={submit}><p className="correction-warning">This creates a corrected fact and preserves the original.</p><label>Value<input required type="number" min="0.0001" max="5000" step="any" value={form.value} onChange={(event) => { setForm({ ...form, value: event.target.value }); }} /></label><label>Unit<select value={form.unit} onChange={(event) => { setForm({ ...form, unit: event.target.value as Weight["unit"] }); }}><option value="lb">lb</option><option value="kg">kg</option></select></label><label>Measurement setting<select value={form.measurementSetting} onChange={(event) => { setForm({ ...form, measurementSetting: event.target.value as Weight["measurement_setting"] }); }}><option value="home">Home</option><option value="provider">Provider / clinic</option></select></label><label>Experienced local time<input required type="datetime-local" value={form.localTime} onChange={(event) => { setForm({ ...form, localTime: event.target.value }); }} /></label><label>Timezone<input required value={form.timezone} onChange={(event) => { setForm({ ...form, timezone: event.target.value }); }} /></label><label className="form-wide">Notes<textarea value={form.notes} onChange={(event) => { setForm({ ...form, notes: event.target.value }); }} /></label><label className="form-wide">Correction reason<input required value={form.reason} onChange={(event) => { setForm({ ...form, reason: event.target.value }); }} /></label>{validation === null ? null : <p className="error-summary form-wide" role="alert">{validation}</p>}{mutation.isError ? <p className="error-summary form-wide" role="alert">The correction was not saved.</p> : null}<div className="filter-actions form-wide"><button type="submit" disabled={mutation.isPending}>Save corrected fact</button><button className="button-secondary" type="button" onClick={close}>Cancel</button></div></form>;
}

function TemperatureCorrection({ record, close }: { record: Temperature; close: () => void }): React.JSX.Element {
  const queryClient = useQueryClient();
  const [form, setForm] = useState({ value: record.value, unit: record.unit, localTime: record.time.local_time.slice(0, 16), timezone: record.time.timezone, notes: record.notes ?? "", reason: "" });
  const [validation, setValidation] = useState<string | null>(null);
  const mutation = useMutation({ mutationFn: (payload: TemperatureCorrectionInput) => correctTemperature(record.id, payload), onSuccess: async () => { await Promise.all([queryClient.invalidateQueries({ queryKey: ["temperature"] }), queryClient.invalidateQueries({ queryKey: ["timeline"] })]); close(); } });
  function submit(event: React.SyntheticEvent<HTMLFormElement>): void {
    event.preventDefault(); const changes: TemperatureCorrectionInput["changes"] = {};
    if (form.value !== record.value) changes.value = form.value;
    if (form.unit !== record.unit) changes.unit = form.unit;
    if (form.localTime !== record.time.local_time.slice(0, 16) || form.timezone !== record.time.timezone) changes.time = { local_time: form.localTime, timezone: form.timezone };
    if (form.notes !== (record.notes ?? "")) changes.notes = form.notes === "" ? null : form.notes;
    if (form.reason.trim() === "" || Object.keys(changes).length === 0) { setValidation(form.reason.trim() === "" ? "Explain why this fact needs correction." : "Change at least one recorded field."); return; }
    setValidation(null); mutation.mutate({ reason: form.reason.trim(), changes });
  }
  const bounds = form.unit === "f" ? { min: 77, max: 113 } : { min: 25, max: 45 };
  return <form className="correction-form" aria-label="Correct temperature" onSubmit={submit}><p className="correction-warning">This creates a corrected fact and preserves the original.</p><label>Value<input required type="number" min={bounds.min} max={bounds.max} step="0.1" value={form.value} onChange={(event) => { setForm({ ...form, value: event.target.value }); }} /></label><label>Unit<select value={form.unit} onChange={(event) => { setForm({ ...form, unit: event.target.value as Temperature["unit"], value: "" }); }}><option value="f">°F</option><option value="c">°C</option></select></label><label>Experienced local time<input required type="datetime-local" value={form.localTime} onChange={(event) => { setForm({ ...form, localTime: event.target.value }); }} /></label><label>Timezone<input required value={form.timezone} onChange={(event) => { setForm({ ...form, timezone: event.target.value }); }} /></label><label className="form-wide">Notes<textarea value={form.notes} onChange={(event) => { setForm({ ...form, notes: event.target.value }); }} /></label><label className="form-wide">Correction reason<input required value={form.reason} onChange={(event) => { setForm({ ...form, reason: event.target.value }); }} /></label>{validation === null ? null : <p className="error-summary form-wide" role="alert">{validation}</p>}{mutation.isError ? <p className="error-summary form-wide" role="alert">The correction was not saved. Use 77–113 °F or 25–45 °C.</p> : null}<div className="filter-actions form-wide"><button type="submit" disabled={mutation.isPending}>Save corrected fact</button><button className="button-secondary" type="button" onClick={close}>Cancel</button></div></form>;
}

export function HealthDataPage(): React.JSX.Element {
  const { session } = useAuth();
  const timezone = session?.user.defaultTimezone ?? "UTC";
  const [searchParams, setSearchParams] = useSearchParams();
  const appliedSearch = searchParams.toString();
  const view = useMemo(() => viewStateFromSearch(appliedSearch, timezone), [appliedSearch, timezone]);
  const filters = useMemo<HealthDataFilters>(() => ({ dateFrom: view.dateFrom, dateTo: view.dateTo, timezone: view.timezone }), [view.dateFrom, view.dateTo, view.timezone]);
  const [draftState, setDraftState] = useState({ search: appliedSearch, filters });
  const draft = draftState.search === appliedSearch ? draftState.filters : filters;
  const setDraft = (next: HealthDataFilters): void => { setDraftState({ search: appliedSearch, filters: next }); };
  const [filterValidation, setFilterValidation] = useState<string | null>(null);
  const appliedRangeInvalid = filters.dateFrom !== "" && filters.dateTo !== "" && filters.dateFrom > filters.dateTo;
  const hasActiveDates = filters.dateFrom !== "" || filters.dateTo !== "";
  const [editing, setEditing] = useState<string | null>(null);
  const bp = useQuery({ queryKey: ["blood-pressure", filters, view.bpPage], queryFn: () => getBloodPressure(filters, view.bpPage), enabled: !appliedRangeInvalid });
  const weight = useQuery({ queryKey: ["weight", filters, view.weightPage], queryFn: () => getWeight(filters, view.weightPage), enabled: !appliedRangeInvalid });
  const temperature = useQuery({ queryKey: ["temperature", filters, view.temperaturePage], queryFn: () => getTemperature(filters, view.temperaturePage), enabled: !appliedRangeInvalid });
  const garmin = useQuery({ queryKey: ["garmin-records", filters, view.garminPage], queryFn: () => getGarminRecords(filters, view.garminPage), enabled: !appliedRangeInvalid });
  const currentBp = bp.data?.items ?? [];
  const currentWeight = weight.data?.items ?? [];
  const currentTemperature = temperature.data?.items ?? [];
  const bpById = new Map([...currentBp, ...(bp.data?.revisions ?? [])].map((record) => [record.id, record]));
  const weightById = new Map([...currentWeight, ...(weight.data?.revisions ?? [])].map((record) => [record.id, record]));
  const temperatureById = new Map([...currentTemperature, ...(temperature.data?.revisions ?? [])].map((record) => [record.id, record]));
  return <Page title="Health data" description="Record and review blood pressure, weight, body temperature, and Garmin observations as measured facts. HealthCurve does not diagnose or recommend treatment from these values.">
    <section aria-labelledby="quick-entry-heading"><h2 id="quick-entry-heading">Quick entry</h2><div className="vital-entry-grid"><BloodPressureEntry timezone={timezone} /><WeightEntry timezone={timezone} /><TemperatureEntry timezone={timezone} /></div></section>
    <section aria-labelledby="health-data-filter-heading"><h2 id="health-data-filter-heading">Filter recorded health data</h2>
      <Paper component="form" className="filter-panel health-data-filter-panel" withBorder p="md" radius="lg" onSubmit={(event) => { event.preventDefault(); if (draft.dateFrom !== "" && draft.dateTo !== "" && draft.dateFrom > draft.dateTo) { setFilterValidation("From date must be on or before Through date."); return; } setFilterValidation(null); setEditing(null); setSearchParams(searchFromViewState({ ...draft, bpPage: 1, weightPage: 1, temperaturePage: 1, garminPage: 1 })); }}>
        <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md"><TextInput label="From date" type="date" value={draft.dateFrom} onChange={(event) => { setDraft({ ...draft, dateFrom: event.target.value }); }} /><TextInput label="Through date" type="date" value={draft.dateTo} onChange={(event) => { setDraft({ ...draft, dateTo: event.target.value }); }} /><TextInput label="IANA timezone" required aria-label="IANA timezone" value={draft.timezone} onChange={(event) => { setDraft({ ...draft, timezone: event.target.value }); }} /></SimpleGrid>
        {filterValidation === null && !appliedRangeInvalid ? null : <Alert color="red" mt="md" role="alert">{filterValidation ?? "From date must be on or before Through date."}</Alert>}
        <Group className="health-data-filter-actions"><Button type="submit">Apply filters</Button><Button variant="outline" type="button" onClick={() => { setFilterValidation(null); setEditing(null); setDraftState({ search: "", filters: { dateFrom: "", dateTo: "", timezone } }); setSearchParams(new URLSearchParams()); }}>Clear filters</Button></Group>
      </Paper>
      <Text c="dimmed" mt="md">Inclusive calendar dates are interpreted in {timezoneAbbreviation(filters.timezone)}. Records keep their original experienced timezone.</Text>
    </section>
    {bp.isFetching || weight.isFetching || temperature.isFetching || garmin.isFetching ? <Text role="status">Loading recorded health data…</Text> : null}
    {bp.isError || weight.isError || temperature.isError || garmin.isError ? <Alert color="red" role="alert">Some health records could not be loaded. Check the date range and IANA timezone.</Alert> : null}
    <section aria-labelledby="trends-heading"><h2 id="trends-heading">Recorded trends</h2><p className="privacy-note">Charts connect recorded observations only. They do not infer readings between observations; absence of a record is not a zero.</p>
      {currentBp.length === 0 ? <p>{hasActiveDates ? "No blood-pressure readings match the selected dates." : "No blood-pressure readings recorded."}</p> : <AccessibleLineChart title="Blood pressure" summary="Systolic and diastolic measurements on the visible records page." unit="mmHg" timezone={timezone} timezoneReferenceDate={currentBp[0]?.time.local_time.slice(0, 10)} dateRange={dateRange(currentBp)} definition="Each point is one current blood-pressure fact on the visible records page. Missing intervals are not inferred; the table contains every plotted reading." sampleCount={currentBp.length} missingCount={0} xAxisLabel="Experienced date / time" yAxisLabel="Blood pressure" series={bpSeries(currentBp)} />}
      {currentWeight.length === 0 ? <p>{hasActiveDates ? "No weight readings match the selected dates." : "No weight readings recorded."}</p> : <AccessibleLineChart title="Weight" summary="Weight measurements on the visible records page, shown on one consistent pounds scale." unit="lb" timezone={timezone} timezoneReferenceDate={currentWeight[0]?.time.local_time.slice(0, 10)} dateRange={dateRange(currentWeight)} definition="Each point is one current weight fact on the visible records page, converted deterministically to pounds and rounded half up to 0.1 lb using 1 lb = 0.45359237 kg. The chart adds one pound of visual padding above and below the observed range; exact values remain in the chart points and records table. Missing intervals are not inferred." sampleCount={currentWeight.length} missingCount={0} xAxisLabel="Experienced date / time" yAxisLabel="Weight" yPadding={1} compactPlot series={weightSeries(currentWeight)} />}
      {currentTemperature.length === 0 ? <p>{hasActiveDates ? "No temperature readings match the selected dates." : "No temperature readings recorded."}</p> : <AccessibleLineChart title="Body temperature" summary="Body-temperature measurements on the visible records page, shown Fahrenheit first." unit="°F" timezone={timezone} timezoneReferenceDate={currentTemperature[0]?.time.local_time.slice(0, 10)} dateRange={dateRange(currentTemperature)} definition="Each point is one current body-temperature fact, shown in Fahrenheit after deterministic conversion when entered in Celsius. The chart adds one degree of visual padding; exact Fahrenheit and Celsius values remain in the records table. Missing intervals are not inferred." sampleCount={currentTemperature.length} missingCount={0} xAxisLabel="Experienced date / time" yAxisLabel="Body temperature" yPadding={1} compactPlot series={temperatureSeries(currentTemperature)} />}
    </section>
    <section aria-labelledby="garmin-records-heading"><h2 id="garmin-records-heading">Garmin recorded observations</h2><p className="privacy-note">These are provider-imported facts, separate from physician-approved plans and AI analysis. Scores and missing values are not interpreted as medical conclusions.</p>{garmin.data?.records.length === 0 ? <p>No Garmin observations match the selected dates.</p> : garmin.data === undefined ? null : <GarminRecordsTable records={garmin.data.records} />}{garmin.data === undefined ? null : <PaginationControls label="Garmin records" metadata={garmin.data.page} onPageChange={(garminPage) => { setSearchParams(searchFromViewState({ ...view, garminPage })); }} />}</section>
    <section aria-labelledby="bp-records-heading"><h2 id="bp-records-heading">Blood pressure records and provenance</h2>{currentBp.length === 0 ? <p>No blood-pressure readings match the selected dates.</p> : <BloodPressureHistoryTable records={currentBp} byId={bpById} editing={editing} setEditing={setEditing} />}{bp.data === undefined ? null : <PaginationControls label="Blood pressure records" metadata={bp.data.page} onPageChange={(bpPage) => { setEditing(null); setSearchParams(searchFromViewState({ ...view, bpPage })); }} />}</section>
    <section aria-labelledby="weight-records-heading"><h2 id="weight-records-heading">Weight records and provenance</h2>{currentWeight.length === 0 ? <p>No weight readings match the selected dates.</p> : <WeightHistoryTable records={currentWeight} byId={weightById} editing={editing} setEditing={setEditing} />}{weight.data === undefined ? null : <PaginationControls label="Weight records" metadata={weight.data.page} onPageChange={(weightPage) => { setEditing(null); setSearchParams(searchFromViewState({ ...view, weightPage })); }} />}</section>
    <section aria-labelledby="temperature-records-heading"><h2 id="temperature-records-heading">Body temperature records and provenance</h2>{currentTemperature.length === 0 ? <p>No temperature readings match the selected dates.</p> : <TemperatureHistoryTable records={currentTemperature} byId={temperatureById} editing={editing} setEditing={setEditing} />}{temperature.data === undefined ? null : <PaginationControls label="Temperature records" metadata={temperature.data.page} onPageChange={(temperaturePage) => { setEditing(null); setSearchParams(searchFromViewState({ ...view, temperaturePage })); }} />}</section>
  </Page>;
}

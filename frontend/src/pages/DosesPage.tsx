import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { correctDose, getDoses, getMedications, getOpenEpisodes, recordDose, type Dose, type DoseCorrectionInput, type DoseInput, type Medication, type RecordedHistoryFilters } from "../api/client";
import { useAuth } from "../auth/context";
import { Page } from "../components/Page";
import { PaginationControls } from "../components/PaginationControls";
import { formatMeasurement } from "../format";
import { localDateTime, timezoneAbbreviation } from "../time";

interface FormValues {
  amount: string;
  unit: Dose["unit"];
  route: Dose["route"];
  category: Dose["dose_category"];
  localTime: string;
  timezone: string;
  notes: string;
  reason: string;
}

interface DoseViewState extends RecordedHistoryFilters { page: number }

function viewFromSearch(search: string, profileTimezone: string): DoseViewState {
  const params = new URLSearchParams(search);
  const page = params.get("page") ?? "";
  return { dateFrom: params.get("local_date_from") ?? "", dateTo: params.get("local_date_to") ?? "", timezone: params.get("timezone") ?? profileTimezone, page: /^\d+$/.test(page) && Number(page) >= 1 ? Number(page) : 1 };
}

function searchFromView(view: DoseViewState): URLSearchParams {
  const params = new URLSearchParams({ timezone: view.timezone });
  if (view.dateFrom !== "") params.set("local_date_from", view.dateFrom);
  if (view.dateTo !== "") params.set("local_date_to", view.dateTo);
  if (view.page > 1) params.set("page", view.page.toString());
  return params;
}

function initialValues(dose: Dose): FormValues {
  return {
    amount: dose.amount,
    unit: dose.unit,
    route: dose.route,
    category: dose.dose_category,
    localTime: dose.time.local_time.slice(0, 19),
    timezone: dose.time.timezone,
    notes: dose.notes ?? "",
    reason: "",
  };
}

function experiencedTime(dose: Dose): string {
  return `${dose.time.local_time.replace("T", " ").slice(0, 16)} · ${timezoneAbbreviation(dose.time.timezone, dose.time.occurred_at)}`;
}

function changedFields(dose: Dose, values: FormValues): DoseCorrectionInput["changes"] {
  const changes: DoseCorrectionInput["changes"] = {};
  if (values.amount !== dose.amount) changes.amount = values.amount;
  if (values.unit !== dose.unit) changes.unit = values.unit;
  if (values.route !== dose.route) changes.route = values.route;
  if (values.category !== dose.dose_category) changes.category = values.category;
  if (values.notes !== (dose.notes ?? "")) changes.notes = values.notes === "" ? null : values.notes;
  if (values.localTime !== dose.time.local_time.slice(0, 19) || values.timezone !== dose.time.timezone) {
    changes.time = { local_time: values.localTime, timezone: values.timezone };
  }
  return changes;
}

interface DoseEntryValues {
  medicationId: string;
  amount: string;
  unit: DoseInput["unit"];
  route: DoseInput["route"];
  category: DoseInput["category"];
  localTime: string;
  episodeId: string;
  notes: string;
}

function doseEntryDefaults(timezone: string): DoseEntryValues {
  return { medicationId: "", amount: "", unit: "mg", route: "oral", category: "scheduled", localTime: localDateTime(new Date(), timezone).slice(0, 16), episodeId: "", notes: "" };
}

function RecordDoseForm({ timezone }: { timezone: string }): React.JSX.Element {
  const queryClient = useQueryClient();
  const medications = useQuery({ queryKey: ["medications"], queryFn: getMedications });
  const episodes = useQuery({ queryKey: ["open-episodes"], queryFn: getOpenEpisodes });
  const [values, setValues] = useState<DoseEntryValues>(() => doseEntryDefaults(timezone));
  const selected = medications.data?.find((item) => item.id === values.medicationId);
  const mutation = useMutation({
    mutationFn: () => recordDose({
      medication_id: values.medicationId,
      amount: values.amount,
      unit: values.unit,
      route: values.route,
      category: values.category,
      time: { local_time: values.localTime, timezone },
      episode_id: values.episodeId === "" ? null : values.episodeId,
      notes: values.notes.trim() === "" ? null : values.notes.trim(),
    }),
    onSuccess: async () => {
      setValues(doseEntryDefaults(timezone));
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["doses"] }),
        queryClient.invalidateQueries({ queryKey: ["timeline"] }),
        queryClient.invalidateQueries({ queryKey: ["today"] }),
        queryClient.invalidateQueries({ queryKey: ["healthcurve"] }),
      ]);
    },
  });

  function chooseMedication(medication: Medication | undefined): void {
    if (medication === undefined) {
      setValues({ ...values, medicationId: "" });
      return;
    }
    setValues({ ...values, medicationId: medication.id, unit: medication.default_unit, route: medication.default_route });
  }

  return <section className="metric-card" aria-labelledby="record-dose-heading">
    <h2 id="record-dose-heading">Record a dose taken</h2>
    <p>This records what happened; it does not change your physician-approved plan. A dose is regular unless you explicitly select Stress dose.</p>
    <form className="plan-form-grid" aria-label="Record a dose taken" onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}>
      <label>Medication<select required value={values.medicationId} onChange={(event) => { chooseMedication(medications.data?.find((item) => item.id === event.target.value)); }}><option value="">Choose medication</option>{(medications.data ?? []).map((medication) => <option value={medication.id} key={medication.id}>{medication.name}{medication.formulation === null ? "" : ` · ${medication.formulation}`}</option>)}</select></label>
      <label>Amount<input required inputMode="decimal" value={values.amount} onChange={(event) => { setValues({ ...values, amount: event.target.value }); }} /></label>
      <label>Unit<select value={values.unit} onChange={(event) => { setValues({ ...values, unit: event.target.value as DoseInput["unit"] }); }}><option value="mg">mg</option><option value="mcg">mcg</option><option value="ml">ml</option><option value="tablet">tablet</option></select></label>
      <label>Route<select value={values.route} onChange={(event) => { setValues({ ...values, route: event.target.value as DoseInput["route"] }); }}><option value="oral">Oral</option><option value="intramuscular">Intramuscular</option><option value="subcutaneous">Subcutaneous</option><option value="intravenous">Intravenous</option></select></label>
      <label>Dose type<select value={values.category} onChange={(event) => { setValues({ ...values, category: event.target.value as DoseInput["category"] }); }}><option value="scheduled">Regular dose</option><option value="stress">Stress dose / up-dose</option></select><small className="field-hint">Choose Stress dose only for an explicitly intended stress or up-dose.</small></label>
      <label>Experienced local time<input required type="datetime-local" value={values.localTime} onChange={(event) => { setValues({ ...values, localTime: event.target.value }); }} /></label>
      <label>Related episode (optional)<select value={values.episodeId} onChange={(event) => { setValues({ ...values, episodeId: event.target.value }); }}><option value="">No episode link</option>{(episodes.data?.items ?? []).map((episode) => <option value={episode.id} key={episode.id}>{episode.trigger}</option>)}</select><small className="field-hint">Linking an episode adds context but does not change the selected dose type.</small></label>
      <label>Timezone<input value={timezone} readOnly /></label>
      <label className="form-wide">Notes (optional)<textarea value={values.notes} onChange={(event) => { setValues({ ...values, notes: event.target.value }); }} /></label>
      <div className="form-wide"><button type="submit" disabled={mutation.isPending || selected === undefined}>{mutation.isPending ? "Recording…" : "Record dose taken"}</button></div>
      {mutation.isSuccess ? <p className="success-message form-wide" role="status">Dose recorded as a fact.</p> : null}
      {mutation.isError ? <p className="error-summary form-wide" role="alert">The dose was not recorded. Check the amount, time, and medication.</p> : null}
    </form>
  </section>;
}

function CorrectionForm({ dose, onCancel }: { dose: Dose; onCancel: () => void }): React.JSX.Element {
  const queryClient = useQueryClient();
  const [values, setValues] = useState(() => initialValues(dose));
  const [validation, setValidation] = useState<string | null>(null);
  const mutation = useMutation({
    mutationFn: (payload: DoseCorrectionInput) => correctDose(dose.id, payload),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["doses"] }),
        queryClient.invalidateQueries({ queryKey: ["timeline"] }),
        queryClient.invalidateQueries({ queryKey: ["today"] }),
      ]);
      onCancel();
    },
  });

  function submit(event: React.SyntheticEvent<HTMLFormElement>): void {
    event.preventDefault();
    const changes = changedFields(dose, values);
    if (values.reason.trim() === "") {
      setValidation("Explain why this recorded fact needs correction.");
      return;
    }
    if (Object.keys(changes).length === 0) {
      setValidation("Change at least one recorded field.");
      return;
    }
    setValidation(null);
    mutation.mutate({ reason: values.reason.trim(), changes });
  }

  return (
    <form className="correction-form" onSubmit={submit} aria-label={`Correct ${dose.medication_name} dose`}>
      <p className="correction-warning">This creates a corrected fact. The original remains in revision history.</p>
      <label>Amount<input required inputMode="decimal" value={values.amount} onChange={(event) => { setValues({ ...values, amount: event.target.value }); }} /></label>
      <label>Unit<select value={values.unit} onChange={(event) => { setValues({ ...values, unit: event.target.value as Dose["unit"] }); }}><option>mg</option><option>mcg</option><option>ml</option><option>tablet</option></select></label>
      <label>Route<select value={values.route} onChange={(event) => { setValues({ ...values, route: event.target.value as Dose["route"] }); }}><option value="oral">Oral</option><option value="intramuscular">Intramuscular</option><option value="subcutaneous">Subcutaneous</option><option value="intravenous">Intravenous</option></select></label>
      <label>Category<select value={values.category} onChange={(event) => { setValues({ ...values, category: event.target.value as Dose["dose_category"] }); }}><option value="scheduled">Scheduled</option><option value="late">Late</option><option value="replacement">Replacement</option><option value="stress">Stress / up-dose</option><option value="taper">Taper</option><option value="emergency">Emergency</option></select></label>
      <label>Experienced local time<input required type="datetime-local" step="1" value={values.localTime} onChange={(event) => { setValues({ ...values, localTime: event.target.value }); }} /></label>
      <label>Timezone<input required value={values.timezone} onChange={(event) => { setValues({ ...values, timezone: event.target.value }); }} /></label>
      <label className="form-wide">Notes<textarea value={values.notes} onChange={(event) => { setValues({ ...values, notes: event.target.value }); }} /></label>
      <label className="form-wide">Correction reason<input required maxLength={500} value={values.reason} onChange={(event) => { setValues({ ...values, reason: event.target.value }); }} /></label>
      {validation === null ? null : <p className="error-summary form-wide" role="alert">{validation}</p>}
      {mutation.isError ? <p className="error-summary form-wide" role="alert">The correction was not saved. Check the amount, local time, and timezone.</p> : null}
      <div className="filter-actions form-wide"><button type="submit" disabled={mutation.isPending}>{mutation.isPending ? "Saving…" : "Save corrected fact"}</button><button className="button-secondary" type="button" onClick={onCancel}>Cancel</button></div>
    </form>
  );
}

export function DosesPage(): React.JSX.Element {
  const { session } = useAuth();
  const profileTimezone = session?.user.defaultTimezone ?? "UTC";
  const [searchParams, setSearchParams] = useSearchParams();
  const appliedSearch = searchParams.toString();
  const view = useMemo(() => viewFromSearch(appliedSearch, profileTimezone), [appliedSearch, profileTimezone]);
  const filters = useMemo<RecordedHistoryFilters>(() => ({ dateFrom: view.dateFrom, dateTo: view.dateTo, timezone: view.timezone }), [view.dateFrom, view.dateTo, view.timezone]);
  const [draftState, setDraftState] = useState({ search: appliedSearch, filters });
  const draft = draftState.search === appliedSearch ? draftState.filters : filters;
  const setDraft = (next: RecordedHistoryFilters): void => { setDraftState({ search: appliedSearch, filters: next }); };
  const invalidRange = filters.dateFrom !== "" && filters.dateTo !== "" && filters.dateFrom > filters.dateTo;
  const [validation, setValidation] = useState<string | null>(null);
  const doses = useQuery({ queryKey: ["doses", filters, view.page], queryFn: () => getDoses(filters, view.page), enabled: !invalidRange });
  const [editingId, setEditingId] = useState<string | null>(null);
  const byId = new Map([...(doses.data?.items ?? []), ...(doses.data?.revisions ?? [])].map((dose) => [dose.id, dose]));
  const current = doses.data?.items ?? [];

  function historyFor(dose: Dose): Dose[] {
    const history: Dose[] = [];
    let priorId = dose.provenance.supersedes_id ?? null;
    while (priorId !== null) {
      const prior = byId.get(priorId);
      if (prior === undefined) break;
      history.push(prior);
      priorId = prior.provenance.supersedes_id ?? null;
    }
    return history;
  }

  return (
    <Page title="Doses" description="Actual recorded doses and their immutable correction history—not the physician-approved schedule.">
      <RecordDoseForm timezone={profileTimezone} />
      <form className="filter-panel" onSubmit={(event) => { event.preventDefault(); if (draft.dateFrom !== "" && draft.dateTo !== "" && draft.dateFrom > draft.dateTo) { setValidation("From date must be on or before Through date."); return; } setValidation(null); setEditingId(null); setSearchParams(searchFromView({ ...draft, page: 1 })); }}>
        <label>From date<input type="date" value={draft.dateFrom} onChange={(event) => { setDraft({ ...draft, dateFrom: event.target.value }); }} /></label>
        <label>Through date<input type="date" value={draft.dateTo} onChange={(event) => { setDraft({ ...draft, dateTo: event.target.value }); }} /></label>
        <label>IANA timezone<input required value={draft.timezone} onChange={(event) => { setDraft({ ...draft, timezone: event.target.value }); }} /></label>
        {validation === null && !invalidRange ? null : <p className="error-summary form-wide" role="alert">{validation ?? "From date must be on or before Through date."}</p>}
        <div className="filter-actions"><button type="submit">Apply filters</button><button className="button-secondary" type="button" onClick={() => { setValidation(null); setEditingId(null); setDraftState({ search: "", filters: { dateFrom: "", dateTo: "", timezone: profileTimezone } }); setSearchParams(new URLSearchParams()); }}>Clear filters</button></div>
      </form>
      <p className="privacy-note">Inclusive dates use {timezoneAbbreviation(filters.timezone)}. These are actual recorded facts, not scheduled plan entries.</p>
      {doses.isFetching ? <p role="status">Loading recorded doses…</p> : null}
      {doses.isError ? <p className="error-summary" role="alert">Recorded doses could not be loaded.</p> : null}
      {doses.data?.page.total_items === 0 ? <section className="empty-state"><h2>No doses recorded</h2><p>A missing record is not a recorded zero dose.</p></section> : null}
      {current.length === 0 ? null : <div className="table-scroll dose-table-region" tabIndex={0} role="region" aria-label="Recorded doses table">
        <table className="dose-table">
          <caption>Current recorded dose facts ordered by experienced time, latest first. Correction history is preserved.</caption>
          <thead><tr><th scope="col">Experienced time</th><th scope="col">Medication and amount</th><th scope="col">Category and route</th><th scope="col">Source and confirmation</th><th scope="col">Provenance and actions</th></tr></thead>
          <tbody>{current.flatMap((dose) => {
            const history = historyFor(dose);
            const row = <tr key={dose.id} data-dose-id={dose.id}>
              <td className="dose-time"><time dateTime={dose.time.occurred_at}>{dose.time.local_time.replace("T", " ").slice(0, 16)}</time><span>{timezoneAbbreviation(dose.time.timezone, dose.time.occurred_at)}</span></td>
              <th scope="row"><span>{dose.medication_name}</span><span>{formatMeasurement(dose.amount, dose.unit)}</span>{dose.notes === null ? null : <span className="dose-notes">{dose.notes}</span>}</th>
              <td><span>{dose.dose_category.replace("_", " ")}</span><span>{dose.route.replace("_", " ")}</span></td>
              <td><span>{dose.provenance.source_type.replace("_", " ")}</span><span>{dose.provenance.confirmation_state.replace("_", " ")}</span></td>
              <td><span>{dose.provenance.is_correction ? `Corrected · ${dose.provenance.correction_reason ?? "reason recorded"}` : "Original record"}</span><button type="button" onClick={() => { setEditingId(editingId === dose.id ? null : dose.id); }}>{editingId === dose.id ? "Close correction form" : "Correct recorded fact"}</button>{history.length === 0 ? null : <details className="revision-history"><summary>Revision history ({history.length})</summary>{history.map((prior) => <article key={prior.id}><h3>Superseded value</h3><p>{formatMeasurement(prior.amount, prior.unit)} · {experiencedTime(prior)} · {prior.route} · {prior.dose_category}</p><p>Source: {prior.provenance.source_type.replace("_", " ")} · Confirmation: {prior.provenance.confirmation_state.replace("_", " ")}</p></article>)}</details>}</td>
            </tr>;
            return editingId === dose.id ? [row, <tr className="correction-table-row" key={`${dose.id}-correction`}><td colSpan={5}><CorrectionForm dose={dose} onCancel={() => { setEditingId(null); }} /></td></tr>] : [row];
          })}</tbody>
        </table>
      </div>}
      {doses.data === undefined ? null : <PaginationControls label="Recorded doses" metadata={doses.data.page} onPageChange={(page) => { setEditingId(null); setSearchParams(searchFromView({ ...view, page })); }} />}
    </Page>
  );
}

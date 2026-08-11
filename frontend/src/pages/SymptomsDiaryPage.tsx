import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { correctSymptom, getDiaryEntries, getLifeEvents, getSymptoms, type Symptom, type SymptomCorrectionInput, type SymptomsDiaryFilters } from "../api/client";
import { useAuth } from "../auth/context";
import { Page } from "../components/Page";
import { PaginationControls } from "../components/PaginationControls";
import { timezoneAbbreviation } from "../time";

function localTime(value: string): string { return value.replace("T", " ").slice(0, 16); }

function words(value: string): string { return value.replaceAll("_", " "); }

interface ViewState extends SymptomsDiaryFilters { symptomPage: number; diaryPage: number; lifePage: number }

function pageNumber(params: URLSearchParams, name: string): number {
  const value = params.get(name) ?? "";
  return /^\d+$/.test(value) && Number(value) >= 1 ? Number(value) : 1;
}

function stateFromSearch(search: string, profileTimezone: string): ViewState {
  const params = new URLSearchParams(search);
  return {
    dateFrom: params.get("local_date_from") ?? "",
    dateTo: params.get("local_date_to") ?? "",
    timezone: params.get("timezone") ?? profileTimezone,
    includeSensitive: params.get("include_sensitive") === "true",
    symptomPage: pageNumber(params, "symptom_page"),
    diaryPage: pageNumber(params, "diary_page"),
    lifePage: pageNumber(params, "life_page"),
  };
}

function searchFromState(state: ViewState): URLSearchParams {
  const params = new URLSearchParams({ timezone: state.timezone });
  if (state.dateFrom !== "") params.set("local_date_from", state.dateFrom);
  if (state.dateTo !== "") params.set("local_date_to", state.dateTo);
  if (state.includeSensitive) params.set("include_sensitive", "true");
  if (state.symptomPage > 1) params.set("symptom_page", state.symptomPage.toString());
  if (state.diaryPage > 1) params.set("diary_page", state.diaryPage.toString());
  if (state.lifePage > 1) params.set("life_page", state.lifePage.toString());
  return params;
}

function SymptomCorrectionForm({ symptom, close }: { symptom: Symptom; close: () => void }): React.JSX.Element {
  const queryClient = useQueryClient();
  const [name, setName] = useState(symptom.name);
  const [severity, setSeverity] = useState(symptom.severity?.toString() ?? "");
  const [bodyArea, setBodyArea] = useState(symptom.body_area ?? "");
  const [notes, setNotes] = useState(symptom.notes ?? "");
  const [local, setLocal] = useState(symptom.time.local_time.slice(0, 19));
  const [timezone, setTimezone] = useState(symptom.time.timezone);
  const [reason, setReason] = useState("");
  const [validation, setValidation] = useState<string | null>(null);
  const mutation = useMutation({ mutationFn: (payload: SymptomCorrectionInput) => correctSymptom(symptom.id, payload), onSuccess: async () => { await Promise.all([queryClient.invalidateQueries({ queryKey: ["symptoms"] }), queryClient.invalidateQueries({ queryKey: ["timeline"] })]); close(); } });

  function submit(event: React.SyntheticEvent<HTMLFormElement>): void {
    event.preventDefault();
    const changes: SymptomCorrectionInput["changes"] = {};
    if (name !== symptom.name) changes.name = name;
    if (severity !== (symptom.severity?.toString() ?? "")) changes.severity = severity === "" ? null : Number(severity);
    if (bodyArea !== (symptom.body_area ?? "")) changes.body_area = bodyArea === "" ? null : bodyArea;
    if (notes !== (symptom.notes ?? "")) changes.notes = notes === "" ? null : notes;
    if (local !== symptom.time.local_time.slice(0, 19) || timezone !== symptom.time.timezone) changes.time = { local_time: local, timezone };
    if (reason.trim() === "") { setValidation("Explain why this recorded fact needs correction."); return; }
    if (Object.keys(changes).length === 0) { setValidation("Change at least one recorded field."); return; }
    setValidation(null);
    mutation.mutate({ reason: reason.trim(), changes });
  }

  return <form className="correction-form" aria-label={`Correct ${symptom.name} symptom`} onSubmit={submit}>
    <p className="correction-warning">This creates a corrected fact and preserves the original.</p>
    <label>Name<input required value={name} onChange={(event) => { setName(event.target.value); }} /></label>
    <label>Severity (0–10)<input type="number" min="0" max="10" value={severity} onChange={(event) => { setSeverity(event.target.value); }} /></label>
    <label>Body area<input value={bodyArea} onChange={(event) => { setBodyArea(event.target.value); }} /></label>
    <label>Experienced local time<input required type="datetime-local" step="1" value={local} onChange={(event) => { setLocal(event.target.value); }} /></label>
    <label>Timezone<input required value={timezone} onChange={(event) => { setTimezone(event.target.value); }} /></label>
    <label className="form-wide">Notes<textarea value={notes} onChange={(event) => { setNotes(event.target.value); }} /></label>
    <label className="form-wide">Correction reason<input required value={reason} onChange={(event) => { setReason(event.target.value); }} /></label>
    {validation === null ? null : <p className="error-summary form-wide" role="alert">{validation}</p>}
    {mutation.isError ? <p className="error-summary form-wide" role="alert">The correction was not saved. Check the values and timezone.</p> : null}
    <div className="filter-actions form-wide"><button type="submit" disabled={mutation.isPending}>Save corrected fact</button><button type="button" className="button-secondary" onClick={close}>Cancel</button></div>
  </form>;
}

export function SymptomsDiaryPage(): React.JSX.Element {
  const { session } = useAuth();
  const profileTimezone = session?.user.defaultTimezone ?? "UTC";
  const [searchParams, setSearchParams] = useSearchParams();
  const appliedSearch = searchParams.toString();
  const view = useMemo(() => stateFromSearch(appliedSearch, profileTimezone), [appliedSearch, profileTimezone]);
  const filters = useMemo<SymptomsDiaryFilters>(() => ({ dateFrom: view.dateFrom, dateTo: view.dateTo, timezone: view.timezone, includeSensitive: view.includeSensitive }), [view.dateFrom, view.dateTo, view.timezone, view.includeSensitive]);
  const [draftState, setDraftState] = useState({ search: appliedSearch, filters });
  const draft = draftState.search === appliedSearch ? draftState.filters : filters;
  const setDraft = (next: SymptomsDiaryFilters): void => { setDraftState({ search: appliedSearch, filters: next }); };
  const invalidRange = filters.dateFrom !== "" && filters.dateTo !== "" && filters.dateFrom > filters.dateTo;
  const [validation, setValidation] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const symptoms = useQuery({ queryKey: ["symptoms", filters, view.symptomPage], queryFn: () => getSymptoms(filters, view.symptomPage), enabled: !invalidRange });
  const diary = useQuery({ queryKey: ["diary", filters, view.diaryPage], queryFn: () => getDiaryEntries(filters, view.diaryPage), enabled: !invalidRange });
  const life = useQuery({ queryKey: ["life-events", filters, view.lifePage], queryFn: () => getLifeEvents(filters, view.lifePage), enabled: !invalidRange });
  const currentSymptoms = symptoms.data?.items ?? [];
  const symptomById = new Map([...currentSymptoms, ...(symptoms.data?.revisions ?? [])].map((item) => [item.id, item]));

  function historyFor(item: Symptom): Symptom[] { const history: Symptom[] = []; let id = item.provenance.supersedes_id ?? null; while (id !== null) { const prior = symptomById.get(id); if (prior === undefined) break; history.push(prior); id = prior.provenance.supersedes_id ?? null; } return history; }
  const loading = symptoms.isFetching || diary.isFetching || life.isFetching;
  const failed = symptoms.isError || diary.isError || life.isError;

  return <Page title="Symptoms & diary" description="Subjective symptoms, diary notes, and life events are recorded facts—not diagnoses or causal claims.">
    <form className="filter-panel" onSubmit={(event) => { event.preventDefault(); if (draft.dateFrom !== "" && draft.dateTo !== "" && draft.dateFrom > draft.dateTo) { setValidation("From date must be on or before Through date."); return; } setValidation(null); setEditing(null); setSearchParams(searchFromState({ ...draft, symptomPage: 1, diaryPage: 1, lifePage: 1 })); }}>
      <label>From date<input type="date" value={draft.dateFrom} onChange={(event) => { setDraft({ ...draft, dateFrom: event.target.value }); }} /></label>
      <label>Through date<input type="date" value={draft.dateTo} onChange={(event) => { setDraft({ ...draft, dateTo: event.target.value }); }} /></label>
      <label>IANA timezone<input required value={draft.timezone} onChange={(event) => { setDraft({ ...draft, timezone: event.target.value }); }} /></label>
      <label className="checkbox-label"><input type="checkbox" checked={draft.includeSensitive} onChange={(event) => { setDraft({ ...draft, includeSensitive: event.target.checked }); }} /> Reveal sensitive diary and life-event entries</label>
      {validation === null && !invalidRange ? null : <p className="error-summary form-wide" role="alert">{validation ?? "From date must be on or before Through date."}</p>}
      <div className="filter-actions"><button type="submit">Apply filters</button><button className="button-secondary" type="button" onClick={() => { setValidation(null); setEditing(null); setDraftState({ search: "", filters: { dateFrom: "", dateTo: "", timezone: profileTimezone, includeSensitive: false } }); setSearchParams(new URLSearchParams()); }}>Clear filters</button></div>
    </form>
    <p className="privacy-note">Inclusive dates use {timezoneAbbreviation(filters.timezone)}. Sensitive text is hidden by default and appears only after applying the reveal control.</p>
    {loading ? <p role="status">Loading recorded symptoms and notes…</p> : null}
    {failed ? <p className="error-summary" role="alert">Some recorded facts could not be loaded.</p> : null}

    <section aria-labelledby="symptoms-heading"><h2 id="symptoms-heading">Symptoms</h2>
      {symptoms.data?.page.total_items === 0 ? <p>No symptoms recorded.</p> : null}
      {currentSymptoms.length === 0 ? null : <div className="table-scroll" tabIndex={0} role="region" aria-label="Symptom records table"><table className="symptom-records-table"><caption>Current symptom facts, latest experienced time first, with correction history.</caption><thead><tr><th scope="col">Experienced time</th><th scope="col">Symptom</th><th scope="col">Severity and area</th><th scope="col">Source and status</th><th scope="col">Notes and actions</th></tr></thead><tbody>{currentSymptoms.flatMap((item) => { const history = historyFor(item); const row = <tr key={item.id}><td className="timeline-time">{localTime(item.time.local_time)}<span>{timezoneAbbreviation(item.time.timezone, item.time.occurred_at)}</span></td><th scope="row">{item.name}</th><td>{item.severity === null ? "Not recorded" : `${item.severity.toString()}/10`}{item.body_area === null ? null : <span>{item.body_area}</span>}</td><td className="symptom-records-table__provenance"><span>{words(item.provenance.source_type)}</span><span>{words(item.provenance.confirmation_state)}</span><span>{item.provenance.is_correction ? `Corrected · ${item.provenance.correction_reason ?? "reason recorded"}` : "Original record"}</span></td><td className="symptom-records-table__actions"><span>{item.notes ?? <span className="missing-value">None</span>}</span><button type="button" onClick={() => { setEditing(editing === item.id ? null : item.id); }}>{editing === item.id ? "Close correction form" : "Correct recorded symptom"}</button>{history.length === 0 ? null : <details className="revision-history"><summary>Revision history ({history.length})</summary>{history.map((prior) => <article key={prior.id}><p>{prior.name}{prior.severity === null ? "" : ` · severity ${prior.severity.toString()}/10`} · {localTime(prior.time.local_time)} · {timezoneAbbreviation(prior.time.timezone, prior.time.occurred_at)}</p>{prior.body_area === null ? null : <p>Body area: {prior.body_area}</p>}{prior.notes === null ? null : <p>Notes: {prior.notes}</p>}</article>)}</details>}</td></tr>; return editing === item.id ? [row, <tr className="correction-table-row" key={`${item.id}-correction`}><td colSpan={5}><SymptomCorrectionForm symptom={item} close={() => { setEditing(null); }} /></td></tr>] : [row]; })}</tbody></table></div>}
      {symptoms.data === undefined ? null : <PaginationControls label="Symptom records" metadata={symptoms.data.page} onPageChange={(symptomPage) => { setEditing(null); setSearchParams(searchFromState({ ...view, symptomPage })); }} />}
    </section>

    <section aria-labelledby="diary-heading"><h2 id="diary-heading">Diary</h2>
      {diary.data?.page.total_items === 0 ? <p>No {filters.includeSensitive ? "" : "non-sensitive "}diary entries match.</p> : null}
      {diary.data === undefined || diary.data.items.length === 0 ? null : <div className="table-scroll" tabIndex={0} role="region" aria-label="Diary records table"><table><caption>Recorded diary facts. Sensitive text appears only when explicitly revealed.</caption><thead><tr><th scope="col">Experienced time</th><th scope="col">Entry</th><th scope="col">Privacy and tags</th><th scope="col">Source and confirmation</th></tr></thead><tbody>{diary.data.items.map((item) => <tr key={item.id}><td className="timeline-time">{localTime(item.time.local_time)}<span>{timezoneAbbreviation(item.time.timezone, item.time.occurred_at)}</span></td><th scope="row">{item.text}</th><td>{item.is_sensitive ? "Sensitive" : "Not marked sensitive"}{item.tags === null || item.tags.length === 0 ? null : <span>{item.tags}</span>}</td><td><span>{words(item.provenance.source_type)}</span><span>{words(item.provenance.confirmation_state)}</span></td></tr>)}</tbody></table></div>}
      {diary.data === undefined ? null : <PaginationControls label="Diary records" metadata={diary.data.page} onPageChange={(diaryPage) => { setSearchParams(searchFromState({ ...view, diaryPage })); }} />}
    </section>

    <section aria-labelledby="life-heading"><h2 id="life-heading">Life events</h2>
      {life.data?.page.total_items === 0 ? <p>No {filters.includeSensitive ? "" : "non-sensitive "}life events match.</p> : null}
      {life.data === undefined || life.data.items.length === 0 ? null : <div className="table-scroll" tabIndex={0} role="region" aria-label="Life event records table"><table><caption>Recorded life-event facts. Sensitive text appears only when explicitly revealed.</caption><thead><tr><th scope="col">Experienced time</th><th scope="col">Event</th><th scope="col">Category and description</th><th scope="col">Privacy, source, and confirmation</th></tr></thead><tbody>{life.data.items.map((item) => <tr key={item.id}><td className="timeline-time">{localTime(item.time.local_time)}<span>{timezoneAbbreviation(item.time.timezone, item.time.occurred_at)}</span></td><th scope="row">{item.title}</th><td><span>{words(item.life_category)}</span>{item.description === null ? <span className="missing-value">No description</span> : <span>{item.description}</span>}</td><td><span>{item.is_sensitive ? "Sensitive" : "Not marked sensitive"}</span><span>{words(item.provenance.source_type)}</span><span>{words(item.provenance.confirmation_state)}</span></td></tr>)}</tbody></table></div>}
      {life.data === undefined ? null : <PaginationControls label="Life event records" metadata={life.data.page} onPageChange={(lifePage) => { setSearchParams(searchFromState({ ...view, lifePage })); }} />}
    </section>
  </Page>;
}

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { correctSymptom, getDiaryEntries, getLifeEvents, getSymptoms, type Symptom, type SymptomCorrectionInput } from "../api/client";
import { FactCard } from "../components/CategoryCards";
import { Page } from "../components/Page";
import { PaginationControls } from "../components/PaginationControls";

function localTime(value: string): string { return value.replace("T", " ").slice(0, 16); }

function Provenance({ record }: { record: Symptom }): React.JSX.Element {
  return <p>Experienced {localTime(record.time.local_time)} · {record.time.timezone} · Source: {record.provenance.source_type.replace("_", " ")} · Confirmation: {record.provenance.confirmation_state.replace("_", " ")}</p>;
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
  const [includeSensitive, setIncludeSensitive] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [symptomPage, setSymptomPage] = useState(1);
  const [diaryPage, setDiaryPage] = useState(1);
  const [lifePage, setLifePage] = useState(1);
  const symptoms = useQuery({ queryKey: ["symptoms", symptomPage], queryFn: () => getSymptoms(symptomPage) });
  const diary = useQuery({ queryKey: ["diary", includeSensitive, diaryPage], queryFn: () => getDiaryEntries(diaryPage, includeSensitive) });
  const life = useQuery({ queryKey: ["life-events", includeSensitive, lifePage], queryFn: () => getLifeEvents(lifePage, includeSensitive) });
  const currentSymptoms = symptoms.data?.items ?? [];
  const symptomById = new Map([...currentSymptoms, ...(symptoms.data?.revisions ?? [])].map((item) => [item.id, item]));

  function historyFor(item: Symptom): Symptom[] { const history: Symptom[] = []; let id = item.provenance.supersedes_id ?? null; while (id !== null) { const prior = symptomById.get(id); if (prior === undefined) break; history.push(prior); id = prior.provenance.supersedes_id ?? null; } return history; }
  const loading = symptoms.isPending || diary.isPending || life.isPending;
  const failed = symptoms.isError || diary.isError || life.isError;

  return <Page title="Symptoms & diary" description="Subjective symptoms, diary notes, and life events are recorded facts—not diagnoses or causal claims.">
    <label className="privacy-toggle"><input type="checkbox" checked={includeSensitive} onChange={(event) => { setIncludeSensitive(event.target.checked); setDiaryPage(1); setLifePage(1); }} /> Reveal sensitive diary and life-event entries</label>
    <p className="privacy-note">Sensitive text is hidden by default and appears only while this control is selected.</p>
    {loading ? <p role="status">Loading recorded symptoms and notes…</p> : null}
    {failed ? <p className="error-summary" role="alert">Some recorded facts could not be loaded.</p> : null}

    <section aria-labelledby="symptoms-heading"><h2 id="symptoms-heading">Symptoms</h2>
      {symptoms.data?.page.total_items === 0 ? <p>No symptoms recorded.</p> : null}
      {currentSymptoms.map((item) => { const history = historyFor(item); return <FactCard key={item.id} title={`${item.name}${item.severity === null ? "" : ` · severity ${item.severity.toString()}/10`}`} metadata={<span>{item.provenance.is_correction ? `Corrected · ${item.provenance.correction_reason ?? "reason recorded"}` : "Original record"}</span>}>
        <Provenance record={item} />
        <button type="button" onClick={() => { setEditing(editing === item.id ? null : item.id); }}>{editing === item.id ? "Close correction form" : "Correct recorded symptom"}</button>
        {editing === item.id ? <SymptomCorrectionForm symptom={item} close={() => { setEditing(null); }} /> : null}
        {history.length === 0 ? null : <details className="revision-history"><summary>Revision history ({history.length})</summary>{history.map((prior) => <article key={prior.id}><p>{prior.name}{prior.severity === null ? "" : ` · severity ${prior.severity.toString()}/10`} · {localTime(prior.time.local_time)} · {prior.time.timezone}</p>{prior.body_area === null ? null : <p>Body area: {prior.body_area}</p>}{prior.notes === null ? null : <p>Notes: {prior.notes}</p>}</article>)}</details>}
      </FactCard>; })}
      {symptoms.data === undefined ? null : <PaginationControls label="Symptom records" metadata={symptoms.data.page} onPageChange={(nextPage) => { setEditing(null); setSymptomPage(nextPage); }} />}
    </section>

    <section aria-labelledby="diary-heading"><h2 id="diary-heading">Diary</h2>
      {diary.data?.page.total_items === 0 ? <p>No {includeSensitive ? "" : "non-sensitive "}diary entries recorded.</p> : null}
      {diary.data?.items.map((item) => <FactCard key={item.id} title={item.is_sensitive ? "Sensitive diary entry" : "Diary entry"} metadata={<span>{item.provenance.source_type.replace("_", " ")} · {item.provenance.confirmation_state.replace("_", " ")}</span>}><p>{item.text}</p><p>{localTime(item.time.local_time)} · {item.time.timezone}</p></FactCard>)}
      {diary.data === undefined ? null : <PaginationControls label="Diary records" metadata={diary.data.page} onPageChange={setDiaryPage} />}
    </section>

    <section aria-labelledby="life-heading"><h2 id="life-heading">Life events</h2>
      {life.data?.page.total_items === 0 ? <p>No {includeSensitive ? "" : "non-sensitive "}life events recorded.</p> : null}
      {life.data?.items.map((item) => <FactCard key={item.id} title={item.title} metadata={<span>{item.provenance.source_type.replace("_", " ")} · {item.provenance.confirmation_state.replace("_", " ")}</span>}><p>{item.life_category.replace("_", " ")}</p>{item.description === null ? null : <p>{item.description}</p>}<p>{localTime(item.time.local_time)} · {item.time.timezone}</p></FactCard>)}
      {life.data === undefined ? null : <PaginationControls label="Life event records" metadata={life.data.page} onPageChange={setLifePage} />}
    </section>
  </Page>;
}

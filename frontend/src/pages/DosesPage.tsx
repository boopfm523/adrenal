import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { correctDose, getDoses, type Dose, type DoseCorrectionInput } from "../api/client";
import { FactCard } from "../components/CategoryCards";
import { Page } from "../components/Page";

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
  return `${dose.time.local_time.replace("T", " ").slice(0, 16)} · ${dose.time.timezone}`;
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
  const doses = useQuery({ queryKey: ["doses", "with-history"], queryFn: () => getDoses(true) });
  const [editingId, setEditingId] = useState<string | null>(null);
  const byId = new Map(doses.data?.map((dose) => [dose.id, dose]) ?? []);
  const supersededIds = new Set(doses.data?.flatMap((dose) => dose.provenance.supersedes_id === null ? [] : [dose.provenance.supersedes_id]) ?? []);
  const current = doses.data?.filter((dose) => !supersededIds.has(dose.id)) ?? [];

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
      {doses.isPending ? <p role="status">Loading recorded doses…</p> : null}
      {doses.isError ? <p className="error-summary" role="alert">Recorded doses could not be loaded.</p> : null}
      {doses.data?.length === 0 ? <section className="empty-state"><h2>No doses recorded</h2><p>A missing record is not a recorded zero dose.</p></section> : null}
      {current.map((dose) => {
        const history = historyFor(dose);
        return <FactCard key={dose.id} title={`${dose.medication_name} · ${dose.amount} ${dose.unit}`} metadata={<span>{dose.provenance.is_correction ? `Corrected · ${dose.provenance.correction_reason ?? "reason recorded"}` : "Original record"}</span>}>
          <p>{experiencedTime(dose)} · {dose.route} · {dose.dose_category}</p>
          <p>Source: {dose.provenance.source_type.replace("_", " ")} · Confirmation: {dose.provenance.confirmation_state.replace("_", " ")}</p>
          <button type="button" onClick={() => { setEditingId(editingId === dose.id ? null : dose.id); }}>{editingId === dose.id ? "Close correction form" : "Correct recorded fact"}</button>
          {editingId === dose.id ? <CorrectionForm dose={dose} onCancel={() => { setEditingId(null); }} /> : null}
          {history.length === 0 ? null : <details className="revision-history"><summary>Revision history ({history.length})</summary>{history.map((prior) => <article key={prior.id}><h3>Superseded value</h3><p>{prior.amount} {prior.unit} · {experiencedTime(prior)} · {prior.route} · {prior.dose_category}</p></article>)}</details>}
        </FactCard>;
      })}
    </Page>
  );
}

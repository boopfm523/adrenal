import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { correctDose, getDoses, type Dose, type DoseCorrectionInput } from "../api/client";
import { Page } from "../components/Page";
import { PaginationControls } from "../components/PaginationControls";
import { formatMeasurement } from "../format";

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
  const [page, setPage] = useState(1);
  const doses = useQuery({ queryKey: ["doses", page], queryFn: () => getDoses(page) });
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
      {doses.isPending ? <p role="status">Loading recorded doses…</p> : null}
      {doses.isError ? <p className="error-summary" role="alert">Recorded doses could not be loaded.</p> : null}
      {doses.data?.page.total_items === 0 ? <section className="empty-state"><h2>No doses recorded</h2><p>A missing record is not a recorded zero dose.</p></section> : null}
      {current.length === 0 ? null : <div className="table-scroll dose-table-region" tabIndex={0} role="region" aria-label="Recorded doses table">
        <table className="dose-table">
          <caption>Current recorded dose facts ordered by experienced time, latest first. Correction history is preserved.</caption>
          <thead><tr><th scope="col">Experienced time</th><th scope="col">Medication and amount</th><th scope="col">Category and route</th><th scope="col">Source and confirmation</th><th scope="col">Provenance and actions</th></tr></thead>
          <tbody>{current.flatMap((dose) => {
            const history = historyFor(dose);
            const row = <tr key={dose.id} data-dose-id={dose.id}>
              <td className="dose-time"><time dateTime={dose.time.occurred_at}>{dose.time.local_time.replace("T", " ").slice(0, 16)}</time><span>{dose.time.timezone}</span></td>
              <th scope="row"><span>{dose.medication_name}</span><span>{formatMeasurement(dose.amount, dose.unit)}</span>{dose.notes === null ? null : <span className="dose-notes">{dose.notes}</span>}</th>
              <td><span>{dose.dose_category.replace("_", " ")}</span><span>{dose.route.replace("_", " ")}</span></td>
              <td><span>{dose.provenance.source_type.replace("_", " ")}</span><span>{dose.provenance.confirmation_state.replace("_", " ")}</span></td>
              <td><span>{dose.provenance.is_correction ? `Corrected · ${dose.provenance.correction_reason ?? "reason recorded"}` : "Original record"}</span><button type="button" onClick={() => { setEditingId(editingId === dose.id ? null : dose.id); }}>{editingId === dose.id ? "Close correction form" : "Correct recorded fact"}</button>{history.length === 0 ? null : <details className="revision-history"><summary>Revision history ({history.length})</summary>{history.map((prior) => <article key={prior.id}><h3>Superseded value</h3><p>{formatMeasurement(prior.amount, prior.unit)} · {experiencedTime(prior)} · {prior.route} · {prior.dose_category}</p><p>Source: {prior.provenance.source_type.replace("_", " ")} · Confirmation: {prior.provenance.confirmation_state.replace("_", " ")}</p></article>)}</details>}</td>
            </tr>;
            return editingId === dose.id ? [row, <tr className="correction-table-row" key={`${dose.id}-correction`}><td colSpan={5}><CorrectionForm dose={dose} onCancel={() => { setEditingId(null); }} /></td></tr>] : [row];
          })}</tbody>
        </table>
      </div>}
      {doses.data === undefined ? null : <PaginationControls label="Recorded doses" metadata={doses.data.page} onPageChange={(nextPage) => { setEditingId(null); setPage(nextPage); }} />}
    </Page>
  );
}

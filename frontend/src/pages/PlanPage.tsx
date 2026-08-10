import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import {
  approveRegimen,
  createMedication,
  createRegimen,
  deleteRegimenDraft,
  getActiveRegimen,
  getMedications,
  getRegimenDiff,
  getRegimens,
  retireRegimen,
  updateRegimenDraft,
  type Medication,
  type RegimenInput,
  type RegimenVersion,
} from "../api/client";
import { PlanCard } from "../components/CategoryCards";
import { Page } from "../components/Page";

interface SlotDraft {
  medication_id: string;
  scheduled_local_time: string;
  amount: string;
  unit: "mg" | "mcg" | "ml" | "tablet";
  route: "oral" | "intramuscular" | "subcutaneous" | "intravenous";
  condition: string;
}

interface InstructionDraft {
  category: "illness" | "procedure" | "exercise" | "emergency" | "general";
  title: string;
  body: string;
  authored_by: string;
  authored_on: string;
}

const blankSlot = (medicationId = ""): SlotDraft => ({
  medication_id: medicationId,
  scheduled_local_time: "07:00",
  amount: "",
  unit: "mg",
  route: "oral",
  condition: "",
});

const blankInstruction = (): InstructionDraft => ({
  category: "general",
  title: "",
  body: "",
  authored_by: "",
  authored_on: "",
});

function localInput(value: string | null): string {
  return value === null ? "" : value.replace(/Z$/, "").slice(0, 16);
}

function formString(data: FormData, name: string): string {
  const value = data.get(name);
  return typeof value === "string" ? value : "";
}

function initialSlots(version: RegimenVersion | null): SlotDraft[] {
  const slots = version?.slots ?? [];
  if (slots.length === 0) return [blankSlot()];
  return slots.map((slot) => ({
    medication_id: slot.medication_id,
    scheduled_local_time: slot.scheduled_local_time.slice(0, 5),
    amount: slot.amount,
    unit: slot.unit,
    route: slot.route,
    condition: slot.condition ?? "",
  }));
}

function initialInstructions(version: RegimenVersion | null): InstructionDraft[] {
  if (version === null) return [];
  const instructions = version.instructions;
  if (instructions === undefined) return [];
  return instructions.map((instruction) => ({
    category: instruction.instruction_category,
    title: instruction.title,
    body: instruction.body,
    authored_by: instruction.authored_by,
    authored_on: instruction.authored_on,
  }));
}

function ApprovalProvenance({ version }: { version: RegimenVersion }): React.JSX.Element {
  if (version.status !== "approved") return <p className="draft-warning">Draft plan—not physician approved. This version is not in force.</p>;
  return <dl className="provenance-grid">
    <div><dt>Approved by</dt><dd>{version.approved_by ?? "Provenance missing"}</dd></div>
    <div><dt>Approval source</dt><dd>{version.approval_source ?? "Provenance missing"}</dd></div>
    <div><dt>Approved at</dt><dd>{version.approved_at ?? "Provenance missing"}</dd></div>
    <div><dt>Effective dates</dt><dd>{version.effective_from} through {version.effective_to ?? "ongoing"}</dd></div>
  </dl>;
}

function PlanContents({ version }: { version: RegimenVersion }): React.JSX.Element {
  const slots = version.slots ?? [];
  const instructions = version.instructions ?? [];
  return <>
    <ApprovalProvenance version={version} />
    <h3>Scheduled slots</h3>
    {slots.length === 0 ? <p>No scheduled slots recorded.</p> : <ul className="plan-list">{slots.map((slot) => <li key={slot.id}><strong>{slot.scheduled_local_time.slice(0, 5)}</strong> · {slot.medication_name} · {slot.amount} {slot.unit} · {slot.route}{slot.condition === null ? null : <span> · {slot.condition}</span>}</li>)}</ul>}
    <h3>Physician-authored instructions</h3>
    {instructions.length === 0 ? <p>No instructions recorded in this version.</p> : instructions.map((instruction) => <article className="instruction-card" key={instruction.id}><h4>{instruction.title}</h4><p>{instruction.body}</p><p>Authored by {instruction.authored_by} on {instruction.authored_on}</p></article>)}
  </>;
}

async function invalidatePlans(queryClient: ReturnType<typeof useQueryClient>): Promise<void> {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ["regimens"] }),
    queryClient.invalidateQueries({ queryKey: ["regimen-diff"] }),
  ]);
}

function MedicationCreator({ onCreated }: { onCreated: (medication: Medication) => void }): React.JSX.Element {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: createMedication,
    onSuccess: async (medication) => {
      await queryClient.invalidateQueries({ queryKey: ["medications"] });
      onCreated(medication);
    },
  });
  return <details className="nested-form">
    <summary>Add a medication to the list</summary>
    <form className="plan-form-grid" onSubmit={(event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const data = new FormData(form);
      const strength = formString(data, "strength").trim();
      mutation.mutate({
        name: formString(data, "name"),
        formulation: formString(data, "formulation") || null,
        strength: strength === "" ? null : strength,
        strength_unit: formString(data, "strength_unit") || null,
        default_unit: data.get("default_unit") as "mg" | "mcg" | "ml" | "tablet",
        default_route: data.get("default_route") as "oral" | "intramuscular" | "subcutaneous" | "intravenous",
        active_from: null,
        active_to: null,
        notes: null,
      });
    }}>
      <label>Name<input name="name" required maxLength={200} /></label>
      <label>Formulation<input name="formulation" placeholder="tablet" maxLength={120} /></label>
      <label>Strength<input name="strength" type="number" min="0.0001" step="any" inputMode="decimal" /></label>
      <label>Strength unit<input name="strength_unit" maxLength={16} placeholder="mg" /></label>
      <label>Default dose unit<select name="default_unit" defaultValue="mg"><option value="mg">mg</option><option value="mcg">mcg</option><option value="ml">mL</option><option value="tablet">tablet</option></select></label>
      <label>Default route<select name="default_route" defaultValue="oral"><option value="oral">Oral</option><option value="intramuscular">Intramuscular</option><option value="subcutaneous">Subcutaneous</option><option value="intravenous">Intravenous</option></select></label>
      <button type="submit" disabled={mutation.isPending}>Save medication</button>
      {mutation.isError ? <p className="error-summary" role="alert">Medication was not saved. Check for a duplicate name and strength.</p> : null}
    </form>
  </details>;
}

function PlanEditor({ source, editDraft, medications, onCancel, onSaved }: {
  source: RegimenVersion | null;
  editDraft: RegimenVersion | null;
  medications: Medication[];
  onCancel: () => void;
  onSaved: (message: string) => void;
}): React.JSX.Element {
  const queryClient = useQueryClient();
  const basis = editDraft ?? source;
  const [slots, setSlots] = useState(() => initialSlots(basis));
  const [instructions, setInstructions] = useState(() => initialInstructions(basis));
  const [selectedNewMedication, setSelectedNewMedication] = useState<Medication | null>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => { headingRef.current?.focus(); }, []);
  const mutation = useMutation({
    mutationFn: (payload: RegimenInput) => editDraft === null ? createRegimen(payload) : updateRegimenDraft(editDraft.id, payload),
    onSuccess: async () => {
      await invalidatePlans(queryClient);
      onSaved(editDraft === null ? "Unapproved plan draft created. It is not in force." : "Unapproved plan draft updated. It is not in force.");
    },
  });
  const available = selectedNewMedication === null || medications.some((item) => item.id === selectedNewMedication.id) ? medications : [...medications, selectedNewMedication];

  return <section className="plan-editor" aria-labelledby="plan-editor-heading">
    <h2 id="plan-editor-heading" ref={headingRef} tabIndex={-1}>{editDraft === null ? (source === null ? "Create your first plan draft" : "Create a new plan version") : "Edit unapproved plan draft"}</h2>
    <p className="draft-warning"><strong>This form creates an unapproved draft.</strong> Saving it does not make it your physician-approved plan and does not record any doses as taken.</p>
    <form onSubmit={(event) => {
      event.preventDefault();
      const data = new FormData(event.currentTarget);
      mutation.mutate({
        version_label: formString(data, "version_label"),
        effective_from: formString(data, "effective_from"),
        effective_to: formString(data, "effective_to") || null,
        notes: formString(data, "notes") || null,
        slots: slots.map((slot, index) => ({ ...slot, condition: slot.condition || null, scheduled_local_time: slot.scheduled_local_time, sort_order: index })),
        instructions: instructions.map((instruction, index) => ({ ...instruction, sort_order: index })),
      });
    }}>
      <div className="plan-form-grid">
        <label>Version label<input name="version_label" required maxLength={60} defaultValue={editDraft?.version_label ?? (source === null ? "" : `${source.version_label} — new version`)} /></label>
        <label>Effective from<input name="effective_from" type="datetime-local" required defaultValue={localInput(basis?.effective_from ?? null)} /></label>
        <label>Effective through (optional)<input name="effective_to" type="datetime-local" defaultValue={localInput(basis?.effective_to ?? null)} /></label>
        <label className="wide-field">Draft notes<textarea name="notes" defaultValue={basis?.notes ?? ""} /></label>
      </div>

      <fieldset><legend>Scheduled medication slots</legend>
        <p>These are scheduled plan entries—not records of medicine actually taken.</p>
        {slots.map((slot, index) => <div className="repeatable-row" key={index}>
          <label>Medication<select required value={slot.medication_id} onChange={(event) => { setSlots((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, medication_id: event.target.value } : item)); }}><option value="">Choose medication</option>{available.map((medication) => <option value={medication.id} key={medication.id}>{medication.name}{medication.strength === null ? "" : ` ${medication.strength} ${medication.strength_unit ?? ""}`}</option>)}</select></label>
          <label>Time<input type="time" required value={slot.scheduled_local_time} onChange={(event) => { setSlots((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, scheduled_local_time: event.target.value } : item)); }} /></label>
          <label>Amount<input type="number" required min="0.0001" step="any" inputMode="decimal" value={slot.amount} onChange={(event) => { setSlots((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, amount: event.target.value } : item)); }} /></label>
          <label>Unit<select value={slot.unit} onChange={(event) => { setSlots((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, unit: event.target.value as SlotDraft["unit"] } : item)); }}><option value="mg">mg</option><option value="mcg">mcg</option><option value="ml">mL</option><option value="tablet">tablet</option></select></label>
          <label>Route<select value={slot.route} onChange={(event) => { setSlots((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, route: event.target.value as SlotDraft["route"] } : item)); }}><option value="oral">Oral</option><option value="intramuscular">Intramuscular</option><option value="subcutaneous">Subcutaneous</option><option value="intravenous">Intravenous</option></select></label>
          <label>Condition (optional)<input maxLength={500} value={slot.condition} onChange={(event) => { setSlots((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, condition: event.target.value } : item)); }} /></label>
          <button type="button" className="secondary-button" onClick={() => { setSlots((items) => items.filter((_, itemIndex) => itemIndex !== index)); }}>Remove slot</button>
        </div>)}
        <button type="button" className="secondary-button" onClick={() => { setSlots((items) => [...items, blankSlot(available[0]?.id)]); }}>Add scheduled slot</button>
        <MedicationCreator onCreated={(medication) => { setSelectedNewMedication(medication); setSlots((items) => items.map((item, index) => index === items.length - 1 && item.medication_id === "" ? { ...item, medication_id: medication.id } : item)); }} />
      </fieldset>

      <fieldset><legend>Physician-authored instructions</legend>
        <p>Copy only instructions actually supplied by a clinician. HealthCurve and its AI do not author or approve them.</p>
        {instructions.map((instruction, index) => <div className="repeatable-row" key={index}>
          <label>Category<select value={instruction.category} onChange={(event) => { setInstructions((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, category: event.target.value as InstructionDraft["category"] } : item)); }}><option value="general">General</option><option value="illness">Illness</option><option value="procedure">Procedure</option><option value="exercise">Exercise</option><option value="emergency">Emergency</option></select></label>
          <label>Title<input required maxLength={200} value={instruction.title} onChange={(event) => { setInstructions((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, title: event.target.value } : item)); }} /></label>
          <label className="wide-field">Instruction<textarea required value={instruction.body} onChange={(event) => { setInstructions((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, body: event.target.value } : item)); }} /></label>
          <label>Authored by<input required maxLength={200} value={instruction.authored_by} onChange={(event) => { setInstructions((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, authored_by: event.target.value } : item)); }} /></label>
          <label>Authored on<input type="date" required value={instruction.authored_on} onChange={(event) => { setInstructions((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, authored_on: event.target.value } : item)); }} /></label>
          <button type="button" className="secondary-button" onClick={() => { setInstructions((items) => items.filter((_, itemIndex) => itemIndex !== index)); }}>Remove instruction</button>
        </div>)}
        <button type="button" className="secondary-button" onClick={() => { setInstructions((items) => [...items, blankInstruction()]); }}>Add physician instruction</button>
      </fieldset>
      <div className="form-actions"><button type="submit" disabled={mutation.isPending}>{editDraft === null ? "Save unapproved draft" : "Update unapproved draft"}</button><button type="button" className="secondary-button" onClick={onCancel}>Cancel</button></div>
      {mutation.isError ? <p className="error-summary" role="alert">The draft was not saved. Check the dates, medication entries, and required fields.</p> : null}
    </form>
  </section>;
}

function ApprovalForm({ version, onComplete }: { version: RegimenVersion; onComplete: (message: string) => void }): React.JSX.Element {
  const queryClient = useQueryClient();
  const mutation = useMutation({ mutationFn: (payload: { approved_by: string; approval_source: string; approved_at: string | null; source_document_checksum: null }) => approveRegimen(version.id, payload), onSuccess: async () => { await invalidatePlans(queryClient); onComplete("Plan version approved with the provenance you entered."); } });
  return <details className="approval-form"><summary>Approve this draft</summary>
    <p><strong>This is a human-only action.</strong> Use it only to record a plan that a physician has actually approved. HealthCurve and its AI cannot approve it.</p>
    <form className="plan-form-grid" onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); mutation.mutate({ approved_by: formString(data, "approved_by"), approval_source: formString(data, "approval_source"), approved_at: formString(data, "approved_at") || null, source_document_checksum: null }); }}>
      <label>Approving clinician or role<input name="approved_by" required maxLength={200} /></label>
      <label>Approval source<input name="approval_source" required maxLength={200} placeholder="Consultation, letter, or portal message" /></label>
      <label>Approval time (optional)<input name="approved_at" type="datetime-local" /></label>
      <label className="checkbox-label wide-field"><input type="checkbox" required /> I confirm this records a real clinician-approved plan, not AI advice.</label>
      <button type="submit" disabled={mutation.isPending}>Record physician approval</button>
      {mutation.isError ? <p className="error-summary" role="alert">Approval was not recorded. Check provenance and overlapping effective dates.</p> : null}
    </form>
  </details>;
}

function RetirementForm({ version, onComplete }: { version: RegimenVersion; onComplete: (message: string) => void }): React.JSX.Element {
  const queryClient = useQueryClient();
  const mutation = useMutation({ mutationFn: () => retireRegimen(version.id), onSuccess: async () => { await invalidatePlans(queryClient); onComplete("Plan version retired. Its history remains available."); } });
  return <details><summary>Retire this approved version</summary><p>Retirement ends an ongoing version now and preserves it as history. It does not delete recorded doses.</p><form onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}><label className="checkbox-label"><input type="checkbox" required /> I understand this will remove the version from active use.</label><button type="submit" disabled={mutation.isPending}>Retire and preserve history</button>{mutation.isError ? <p className="error-summary" role="alert">The plan version was not retired.</p> : null}</form></details>;
}

function DraftDeletionForm({ versionId, onDeleted }: { versionId: string; onDeleted: () => void }): React.JSX.Element {
  const queryClient = useQueryClient();
  const mutation = useMutation({ mutationFn: ({ password, confirmation }: { password: string; confirmation: "DELETE DRAFT PLAN" }) => deleteRegimenDraft(versionId, password, confirmation), onSuccess: async () => { onDeleted(); await invalidatePlans(queryClient); } });
  return <details className="draft-delete danger-zone"><summary>Delete this unapproved draft</summary><p>This permanently removes only this draft and its draft slots/instructions. Approved or retired plan history cannot be deleted.</p><form onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); mutation.mutate({ password: data.get("password") as string, confirmation: data.get("confirmation") as "DELETE DRAFT PLAN" }); }}><label>Current password<input name="password" type="password" required autoComplete="current-password" /></label><label>Type DELETE DRAFT PLAN<input name="confirmation" required autoComplete="off" pattern="DELETE DRAFT PLAN" /></label><button type="submit" disabled={mutation.isPending}>Permanently delete draft</button></form>{mutation.isError ? <p className="error-summary" role="alert">Draft was not deleted. Check the password and phrase; referenced or historical plans cannot be deleted.</p> : null}</details>;
}

export function PlanPage(): React.JSX.Element {
  const active = useQuery({ queryKey: ["regimens", "active"], queryFn: getActiveRegimen });
  const history = useQuery({ queryKey: ["regimens", "history"], queryFn: getRegimens });
  const medications = useQuery({ queryKey: ["medications"], queryFn: getMedications });
  const [olderId, setOlderId] = useState("");
  const [newerId, setNewerId] = useState("");
  const [editor, setEditor] = useState<{ source: RegimenVersion | null; edit: RegimenVersion | null } | null>(null);
  const [message, setMessage] = useState("");
  const chronological = [...(history.data ?? [])].sort((a, b) => a.effective_from.localeCompare(b.effective_from));
  const selectedOlderId = olderId !== "" ? olderId : (chronological[chronological.length - 2]?.id ?? "");
  const selectedNewerId = newerId !== "" ? newerId : (chronological[chronological.length - 1]?.id ?? "");
  const diff = useQuery({ queryKey: ["regimen-diff", selectedOlderId, selectedNewerId], queryFn: () => getRegimenDiff(selectedOlderId, selectedNewerId), enabled: selectedOlderId !== "" && selectedNewerId !== "" && selectedOlderId !== selectedNewerId });
  const complete = (nextMessage: string): void => { setEditor(null); setMessage(nextMessage); };

  return <Page title="Medication plan" description="Physician-approved schedules and their provenance, kept separate from actual recorded doses.">
    {(active.isPending || history.isPending || medications.isPending) ? <p role="status">Loading medication plan…</p> : null}
    {(active.isError || history.isError || medications.isError) ? <p className="error-summary" role="alert">Medication plan data could not be loaded.</p> : null}
    {message === "" ? null : <p className="success-message" role="status">{message}</p>}
    {editor === null ? <div className="page-actions"><button type="button" onClick={() => { setMessage(""); setEditor({ source: active.data ?? null, edit: null }); }}>{active.data === null ? "Create first plan draft" : "Create new version from active plan"}</button></div> : null}
    {editor === null || medications.data === undefined ? null : <PlanEditor source={editor.source} editDraft={editor.edit} medications={medications.data} onCancel={() => { setEditor(null); }} onSaved={complete} />}
    {active.data === null ? <section className="empty-state"><h2>No approved plan currently in force</h2><p>Draft and historical versions appear below, but HealthCurve will not treat them as the active plan.</p></section> : null}
    {active.data === undefined || active.data === null ? null : <PlanCard title={`${active.data.version_label} · currently in force`}><PlanContents version={active.data} /></PlanCard>}

    <section aria-labelledby="history-heading"><h2 id="history-heading">Version history</h2><p>Approved and retired versions are immutable history. Edit a draft or create a new version to change a schedule.</p>
      {history.data?.length === 0 ? <p>No plan versions recorded.</p> : null}
      <div className="version-history">{history.data?.map((version) => <article className={`version-card version-card--${version.status}`} key={version.id}><p className="category-label">{version.status === "draft" ? "Draft plan—not physician approved" : version.status === "approved" ? "Physician-approved plan" : "Retired plan version"}</p><h3>{version.version_label}</h3><ApprovalProvenance version={version} /><details><summary>Show slots and instructions</summary><PlanContents version={version} /></details>{version.status === "draft" ? <><div className="form-actions"><button type="button" className="secondary-button" onClick={() => { setMessage(""); setEditor({ source: null, edit: version }); }}>Edit draft</button></div><ApprovalForm version={version} onComplete={complete} /><DraftDeletionForm versionId={version.id} onDeleted={() => { complete("The unapproved draft was permanently deleted. Approved plan history and recorded doses were unchanged."); }} /></> : null}{version.status === "approved" ? <RetirementForm version={version} onComplete={complete} /> : null}</article>)}</div>
    </section>

    <section aria-labelledby="diff-heading"><h2 id="diff-heading">Compare versions</h2>
      {history.data !== undefined && history.data.length < 2 ? <p>At least two versions are needed for a comparison.</p> : <div className="diff-controls"><label>Older version<select value={selectedOlderId} onChange={(event) => { setOlderId(event.target.value); }}>{history.data?.map((version) => <option key={version.id} value={version.id}>{version.version_label}</option>)}</select></label><label>Newer version<select value={selectedNewerId} onChange={(event) => { setNewerId(event.target.value); }}>{history.data?.map((version) => <option key={version.id} value={version.id}>{version.version_label}</option>)}</select></label></div>}
      {selectedOlderId === selectedNewerId && selectedOlderId !== "" ? <p className="error-summary" role="alert">Choose two different versions.</p> : null}
      {diff.isPending && diff.isFetching ? <p role="status">Calculating deterministic version diff…</p> : null}
      {diff.isError ? <p className="error-summary" role="alert">The version comparison could not be loaded.</p> : null}
      {diff.data === undefined ? null : <div className="version-diff">{(["added", "removed", "changed"] as const).map((kind) => <section key={kind}><h3>{kind[0]?.toUpperCase()}{kind.slice(1)}</h3>{diff.data[kind]?.length === 0 ? <p>No {kind} schedule entries.</p> : <ul>{diff.data[kind]?.map((entry) => <li key={entry}>{entry}</li>)}</ul>}</section>)}</div>}
    </section>
  </Page>;
}

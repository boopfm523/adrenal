import { Alert, Button, Checkbox, Group, Paper, SimpleGrid, Table, Text, TextInput, Textarea, Title } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import {
  approveRegimen,
  ApiError,
  createMedication,
  createRegimen,
  deleteRegimen,
  getActiveRegimen,
  getMedications,
  getRegimenDiff,
  getRegimens,
  retireRegimen,
  updateRegimenDraft,
  type Medication,
  type RecordedHistoryFilters,
  type RegimenInput,
  type RegimenVersion,
} from "../api/client";
import { useAuth } from "../auth/context";
import { PlanCard } from "../components/CategoryCards";
import { Page } from "../components/Page";
import { PaginationControls } from "../components/PaginationControls";
import { formatMeasurement, formatQuantitativeText } from "../format";
import { formatUnzonedDateTime, formatZonedDateTime, timezoneAbbreviation } from "../time";

interface SlotDraft {
  medication_id: string;
  timing_mode: "fixed_time" | "wake";
  scheduled_local_time: string;
  reminder_local_time: string;
  amount: string;
  unit: "mg" | "mcg" | "ml" | "tablet";
  route: "oral" | "intramuscular" | "subcutaneous" | "intravenous";
  condition: string;
}

type RegimenSlot = NonNullable<RegimenVersion["slots"]>[number];

interface InstructionDraft {
  category: "illness" | "procedure" | "exercise" | "emergency" | "general";
  title: string;
  body: string;
  authored_by: string;
  authored_on: string;
}

interface PlanHistoryView extends RecordedHistoryFilters { page: number }
function planHistoryView(search: string, profileTimezone: string): PlanHistoryView { const params = new URLSearchParams(search); const rawPage = params.get("page") ?? ""; return { dateFrom: params.get("local_date_from") ?? "", dateTo: params.get("local_date_to") ?? "", timezone: params.get("timezone") ?? profileTimezone, page: /^\d+$/.test(rawPage) && Number(rawPage) >= 1 ? Number(rawPage) : 1 }; }
function planHistorySearch(view: PlanHistoryView): URLSearchParams { const params = new URLSearchParams({ timezone: view.timezone }); if (view.dateFrom !== "") params.set("local_date_from", view.dateFrom); if (view.dateTo !== "") params.set("local_date_to", view.dateTo); if (view.page > 1) params.set("page", view.page.toString()); return params; }

const blankSlot = (medicationId = ""): SlotDraft => ({
  medication_id: medicationId,
  timing_mode: "fixed_time",
  scheduled_local_time: "07:00",
  reminder_local_time: "07:30",
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

function medicationOptionLabel(medication: Medication): string {
  const formulation = medication.formulation === null ? "" : ` ${medication.formulation}`;
  const strength = medication.strength === null
    ? "formulation strength not recorded"
    : `formulation strength: ${formatMeasurement(medication.strength, medication.strength_unit)}`;
  return `${medication.name}${formulation} — ${strength}`;
}

const medicationOptionCollator = new Intl.Collator("en", {
  numeric: true,
  sensitivity: "base",
});

function sortMedicationsByOptionLabel(medications: Medication[]): Medication[] {
  return [...medications].sort((left, right) => {
    const labelOrder = medicationOptionCollator.compare(
      medicationOptionLabel(left),
      medicationOptionLabel(right),
    );
    if (labelOrder !== 0) return labelOrder;
    return left.id.localeCompare(right.id);
  });
}

function localInput(value: string | null): string {
  return value === null ? "" : value.replace(/Z$/, "").slice(0, 16);
}

function formString(data: FormData, name: string): string {
  const value = data.get(name);
  return typeof value === "string" ? value : "";
}

function nextVersionLabel(current: string): string {
  const suffix = " — new version";
  const withoutRepeatedSuffix = current
    .replace(/(?:\s+—\s+new version\d*)+$/iu, "")
    .trim() || "Medication plan";
  return `${withoutRepeatedSuffix.slice(0, 60 - suffix.length).trimEnd()}${suffix}`;
}

function initialSlots(version: RegimenVersion | null): SlotDraft[] {
  const slots = version?.slots ?? [];
  if (slots.length === 0) return [blankSlot()];
  return slots.map((slot) => ({
    medication_id: slot.medication_id,
    timing_mode: slot.timing_mode,
    scheduled_local_time: slot.scheduled_local_time?.slice(0, 5) ?? "07:00",
    reminder_local_time: slot.reminder_local_time?.slice(0, 5) ?? "07:30",
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

function RecordedPlanTime({ value }: { value: string }): React.JSX.Element {
  return <time dateTime={value}>{formatUnzonedDateTime(value)}</time>;
}

function slotTimingLabel(slot: RegimenSlot): string {
  if (slot.timing_mode === "wake") {
    return `When I wake up · remind if unrecorded by ${slot.reminder_local_time?.slice(0, 5) ?? "time not recorded"}`;
  }
  return slot.scheduled_local_time?.slice(0, 5) ?? "Time not recorded";
}

function utcOffsetLabel(offsetMinutes: number | null): string {
  if (offsetMinutes === null) return "UTC offset unavailable";
  const sign = offsetMinutes < 0 ? "−" : "+";
  const absolute = Math.abs(offsetMinutes);
  return `UTC${sign}${String(Math.floor(absolute / 60)).padStart(2, "0")}:${String(absolute % 60).padStart(2, "0")}`;
}

function EffectivePlanTime({ canonical, local, timezone, offsetMinutes }: { canonical: string; local?: string | null; timezone?: string | null; offsetMinutes?: number | null }): React.JSX.Element {
  if (local == null || timezone == null) return <><RecordedPlanTime value={canonical} /> <span>(legacy time; original timezone unknown)</span></>;
  return <><RecordedPlanTime value={local} /> <span>({timezone}, {utcOffsetLabel(offsetMinutes ?? null)})</span></>;
}

function EffectivePeriod({ version }: { version: RegimenVersion }): React.JSX.Element {
  if (version.effective_from === null) {
    return <>Starts when this draft is set live{version.effective_to === null ? "; no end date" : <> through <EffectivePlanTime canonical={version.effective_to} local={version.effective_to_local} timezone={version.effective_timezone} offsetMinutes={version.effective_to_utc_offset_minutes} /></>}</>;
  }
  return <>
    <EffectivePlanTime canonical={version.effective_from} local={version.effective_from_local} timezone={version.effective_timezone} offsetMinutes={version.effective_from_utc_offset_minutes} />
    {" through "}
    {version.effective_to === null ? "ongoing" : <EffectivePlanTime canonical={version.effective_to} local={version.effective_to_local} timezone={version.effective_timezone} offsetMinutes={version.effective_to_utc_offset_minutes} />}
  </>;
}

interface PlanInterval {
  id: string;
  label: string;
  status: RegimenVersion["status"];
  effectiveFrom: string;
  effectiveTo: string | null;
}

interface PlanOverlap {
  first: PlanInterval;
  second: PlanInterval;
  kind: "draft" | "approved";
  from: string;
  to: string | null;
}

function intervalTime(value: string): number {
  const explicitZone = /(?:Z|[+-]\d\d:\d\d)$/.test(value);
  return Date.parse(explicitZone ? value : `${value}Z`);
}

function intervalsOverlap(first: PlanInterval, second: PlanInterval): boolean {
  const firstEnd = first.effectiveTo === null ? Number.POSITIVE_INFINITY : intervalTime(first.effectiveTo);
  const secondEnd = second.effectiveTo === null ? Number.POSITIVE_INFINITY : intervalTime(second.effectiveTo);
  return intervalTime(first.effectiveFrom) < secondEnd && intervalTime(second.effectiveFrom) < firstEnd;
}

function findPlanOverlaps(intervals: PlanInterval[]): PlanOverlap[] {
  const overlaps: PlanOverlap[] = [];
  intervals.forEach((first, index) => {
    intervals.slice(index + 1).forEach((second) => {
      if (!intervalsOverlap(first, second)) return;
      const from = intervalTime(first.effectiveFrom) >= intervalTime(second.effectiveFrom) ? first.effectiveFrom : second.effectiveFrom;
      const firstEnd = first.effectiveTo === null ? Number.POSITIVE_INFINITY : intervalTime(first.effectiveTo);
      const secondEnd = second.effectiveTo === null ? Number.POSITIVE_INFINITY : intervalTime(second.effectiveTo);
      const to = firstEnd === Number.POSITIVE_INFINITY && secondEnd === Number.POSITIVE_INFINITY
        ? null
        : firstEnd <= secondEnd ? first.effectiveTo : second.effectiveTo;
      overlaps.push({ first, second, kind: first.status === "draft" || second.status === "draft" ? "draft" : "approved", from, to });
    });
  });
  return overlaps;
}

function planInterval(version: RegimenVersion): PlanInterval | null {
  return version.effective_from === null ? null : { id: version.id, label: version.version_label, status: version.status, effectiveFrom: version.effective_from, effectiveTo: version.effective_to };
}

function statusLabel(status: RegimenVersion["status"]): string {
  if (status === "draft") return "Draft—not physician approved";
  if (status === "approved") return "Physician-approved";
  return "Retired historical plan";
}

function OverlapList({ overlaps, headingLevel = 3 }: { overlaps: PlanOverlap[]; headingLevel?: 3 | 4 }): React.JSX.Element | null {
  if (overlaps.length === 0) return null;
  const Heading = `h${String(headingLevel)}` as "h3" | "h4";
  return <section className="plan-overlap-summary" aria-labelledby={`plan-overlap-heading-${String(headingLevel)}`}>
    <Heading id={`plan-overlap-heading-${String(headingLevel)}`}>Overlapping effective periods</Heading>
    <ul>{overlaps.map((overlap) => <li key={`${overlap.first.id}-${overlap.second.id}`}>
      <strong>{overlap.kind === "draft" ? "Draft overlap" : "Approved-plan overlap"}:</strong> “{overlap.first.label}” and “{overlap.second.label}” overlap from <RecordedPlanTime value={overlap.from} /> through {overlap.to === null ? "ongoing" : <RecordedPlanTime value={overlap.to} />}.
    </li>)}</ul>
    <p>When a draft is set live, HealthCurve can end one currently live predecessor at the new plan’s start. Other historical or future conflicts must be corrected first.</p>
  </section>;
}

function PlanTimeline({ versions }: { versions: RegimenVersion[] }): React.JSX.Element {
  if (versions.length === 0) return <section className="plan-timeline" aria-labelledby="plan-timeline-heading"><h3 id="plan-timeline-heading">Plan timeline</h3><p>No plan versions to place on the timeline.</p></section>;
  const intervals = versions.flatMap((version) => { const interval = planInterval(version); return interval === null ? [] : [interval]; }).sort((a, b) => intervalTime(a.effectiveFrom) - intervalTime(b.effectiveFrom));
  const pending = versions.filter((version) => version.effective_from === null);
  if (intervals.length === 0) return <section className="plan-timeline" aria-labelledby="plan-timeline-heading"><h3 id="plan-timeline-heading">Plan timeline</h3><p>{pending.length} draft plan{pending.length === 1 ? " is" : "s are"} waiting for a start time. The start will be the moment each draft is set live unless you enter one.</p></section>;
  const overlaps = findPlanOverlaps(intervals);
  const overlappingIds = new Set(overlaps.flatMap((overlap) => [overlap.first.id, overlap.second.id]));
  const starts = intervals.map((interval) => intervalTime(interval.effectiveFrom));
  const finiteEnds = intervals.flatMap((interval) => interval.effectiveTo === null ? [] : [intervalTime(interval.effectiveTo)]);
  const minimum = Math.min(...starts);
  const latestKnown = Math.max(...starts, ...finiteEnds);
  const day = 24 * 60 * 60 * 1000;
  const maximum = Math.max(latestKnown, minimum + day) + day;
  const span = maximum - minimum;
  const versionsById = new Map(versions.map((version) => [version.id, version]));
  return <section className="plan-timeline" aria-labelledby="plan-timeline-heading">
    <h3 id="plan-timeline-heading">Plan timeline</h3>
    <p>Effective periods are shown in date order. An ongoing bar extends to the right edge. Dates describe recorded plan metadata, not dosing advice.</p>
    {pending.length === 0 ? null : <p>{pending.length} draft plan{pending.length === 1 ? " is" : "s are"} not placed on the timeline because the start will be resolved when set live.</p>}
    <ol className="plan-timeline-list">{intervals.map((interval) => {
      const start = intervalTime(interval.effectiveFrom);
      const end = interval.effectiveTo === null ? maximum : Math.min(intervalTime(interval.effectiveTo), maximum);
      const left = Math.max(0, ((start - minimum) / span) * 100);
      const width = Math.max(1.5, ((Math.max(end, start + day / 12) - start) / span) * 100);
      const displayVersion = versionsById.get(interval.id);
      return <li className={`plan-timeline-item plan-timeline-item--${interval.status}${overlappingIds.has(interval.id) ? " plan-timeline-item--overlap" : ""}`} key={interval.id}>
        <div className="plan-timeline-copy"><strong>{interval.label}</strong><span>{statusLabel(interval.status)}</span><span>{displayVersion === undefined ? <><RecordedPlanTime value={interval.effectiveFrom} /> through {interval.effectiveTo === null ? "ongoing" : <RecordedPlanTime value={interval.effectiveTo} />}</> : <EffectivePeriod version={displayVersion} />}</span>{overlappingIds.has(interval.id) ? <span className="plan-timeline-overlap-label">Overlaps another effective period</span> : null}</div>
        <div className="plan-timeline-track" aria-hidden="true"><span style={{ left: `${String(left)}%`, width: `${String(Math.min(width, 100 - left))}%` }} /></div>
      </li>;
    })}</ol>
    <OverlapList overlaps={overlaps} />
  </section>;
}

function ApprovalProvenance({ version, timezone }: { version: RegimenVersion; timezone: string }): React.JSX.Element {
  if (version.status !== "approved") return <p className="draft-warning">Draft plan—not physician approved. This version is not in force.</p>;
  return <dl className="provenance-grid">
    <div><dt>Approved by</dt><dd>{version.approved_by ?? "Provenance missing"}</dd></div>
    <div><dt>Approval source</dt><dd>{version.approval_source ?? "Provenance missing"}</dd></div>
    <div><dt>Approved at</dt><dd>{version.approved_at === null ? "Provenance missing" : <time dateTime={version.approved_at}>{formatZonedDateTime(version.approved_at, timezone)}</time>}</dd></div>
    <div><dt>Effective dates</dt><dd><EffectivePeriod version={version} /></dd></div>
  </dl>;
}

function PlanContents({ version, timezone }: { version: RegimenVersion; timezone: string }): React.JSX.Element {
  const slots = version.slots ?? [];
  const instructions = version.instructions ?? [];
  return <>
    <ApprovalProvenance version={version} timezone={timezone} />
    <h3>Scheduled slots</h3>
    {slots.length === 0 ? <p>No scheduled slots recorded.</p> : <ul className="plan-list">{slots.map((slot) => <li key={slot.id}><strong>{slotTimingLabel(slot)}</strong> · {slot.medication_name} · {formatMeasurement(slot.amount, slot.unit)} · {slot.route}{slot.condition === null ? null : <span> · {slot.condition}</span>}</li>)}</ul>}
    <details className="metric-definition physician-instructions">
      <summary>Physician-authored instructions</summary>
      <div className="physician-instructions__content">
        {instructions.length === 0 ? <p>No instructions recorded in this version.</p> : instructions.map((instruction) => <article className="instruction-card" key={instruction.id}><h4>{instruction.title}</h4><p>{instruction.body}</p><p>Authored by {instruction.authored_by} on {instruction.authored_on}</p></article>)}
      </div>
    </details>
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
    <form className="aligned-form-grid plan-form-grid" onSubmit={(event) => {
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

function PlanEditor({ source, editDraft, medications, existingVersions, timezone, onCancel, onSaved }: {
  source: RegimenVersion | null;
  editDraft: RegimenVersion | null;
  medications: Medication[];
  existingVersions: RegimenVersion[];
  timezone: string;
  onCancel: () => void;
  onSaved: (message: string, version: RegimenVersion) => void;
}): React.JSX.Element {
  const queryClient = useQueryClient();
  const basis = editDraft ?? source;
  const [slots, setSlots] = useState(() => initialSlots(basis));
  const [instructions, setInstructions] = useState(() => initialInstructions(basis));
  const [selectedNewMedication, setSelectedNewMedication] = useState<Medication | null>(null);
  const [effectiveFrom, setEffectiveFrom] = useState(() => editDraft === null ? "" : localInput(editDraft.effective_from_local ?? editDraft.effective_from));
  const [effectiveTo, setEffectiveTo] = useState(() => editDraft === null ? "" : localInput(editDraft.effective_to_local ?? editDraft.effective_to));
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => { headingRef.current?.focus(); }, []);
  const mutation = useMutation({
    mutationFn: (payload: RegimenInput) => editDraft === null ? createRegimen(payload) : updateRegimenDraft(editDraft.id, payload),
    onSuccess: async (version) => {
      await invalidatePlans(queryClient);
      onSaved(editDraft === null ? "Unapproved plan draft created. It is not in force." : "Unapproved plan draft updated. It is not in force.", version);
    },
  });
  const available = sortMedicationsByOptionLabel(
    selectedNewMedication === null || medications.some((item) => item.id === selectedNewMedication.id)
      ? medications
      : [...medications, selectedNewMedication],
  );
  const proposedInterval: PlanInterval | null = effectiveFrom === "" ? null : {
    id: editDraft?.id ?? "proposed-draft",
    label: editDraft?.version_label ?? "Proposed draft",
    status: "draft",
    effectiveFrom,
    effectiveTo: effectiveTo === "" ? null : effectiveTo,
  };
  const proposedOverlaps = proposedInterval === null ? [] : findPlanOverlaps([
    ...existingVersions.filter((version) => version.id !== editDraft?.id).flatMap((version) => { const interval = planInterval(version); return interval === null ? [] : [interval]; }),
    proposedInterval,
  ]).filter((overlap) => overlap.first.id === proposedInterval.id || overlap.second.id === proposedInterval.id);
  const expectedHandoff = proposedOverlaps.find((overlap) =>
    source !== null && (overlap.first.id === source.id || overlap.second.id === source.id)
  );
  const blockingOverlaps = proposedOverlaps.filter((overlap) => overlap !== expectedHandoff);

  return <section className="plan-editor" aria-labelledby="plan-editor-heading">
    <h2 id="plan-editor-heading" ref={headingRef} tabIndex={-1}>{editDraft === null ? (source === null ? "Create your first plan draft" : "Create a new plan version") : "Edit unapproved plan draft"}</h2>
    <p className="draft-warning"><strong>This form creates an unapproved draft.</strong> Saving it does not make it your physician-approved plan and does not record any doses as taken.</p>
    <MedicationCreator onCreated={(medication) => { setSelectedNewMedication(medication); setSlots((items) => items.map((item, index) => index === items.length - 1 && item.medication_id === "" ? { ...item, medication_id: medication.id } : item)); }} />
    <form onSubmit={(event) => {
      event.preventDefault();
      const data = new FormData(event.currentTarget);
      mutation.mutate({
        version_label: formString(data, "version_label"),
        effective_from: formString(data, "effective_from") || null,
        effective_to: formString(data, "effective_to") || null,
        effective_timezone: timezone,
        notes: formString(data, "notes") || null,
        slots: slots.map((slot, index) => ({
          medication_id: slot.medication_id,
          timing_mode: slot.timing_mode,
          scheduled_local_time: slot.timing_mode === "fixed_time" ? slot.scheduled_local_time : null,
          reminder_local_time: slot.timing_mode === "wake" ? slot.reminder_local_time : null,
          amount: slot.amount,
          unit: slot.unit,
          route: slot.route,
          condition: slot.condition || null,
          sort_order: index,
        })),
        instructions: instructions.map((instruction, index) => ({ ...instruction, sort_order: index })),
      });
    }}>
      <div className="aligned-form-grid plan-form-grid">
        <TextInput label="Version label" aria-label="Version label" name="version_label" required maxLength={60} defaultValue={editDraft?.version_label ?? (source === null ? "" : nextVersionLabel(source.version_label))} />
        <TextInput label="Effective start (optional)" name="effective_from" type="datetime-local" aria-describedby="effective-period-help" value={effectiveFrom} onChange={(event) => { setEffectiveFrom(event.target.value); }} />
        <TextInput label="Effective through (optional)" name="effective_to" type="datetime-local" aria-describedby="effective-period-help" value={effectiveTo} min={effectiveFrom || undefined} onChange={(event) => { setEffectiveTo(event.target.value); }} />
        <p className="field-hint wide-field" id="effective-period-help">Leave the start blank to use the exact moment you set this plan live. Enter a start only when the plan should begin at a different time. The end is also optional. Times are entered in {timezone}; the start is included and the end is the first moment this plan no longer applies.</p>
        <aside className="proposed-plan-interval wide-field" aria-live="polite" aria-labelledby="proposed-plan-interval-heading">
          <h3 id="proposed-plan-interval-heading">Proposed effective interval</h3>
          {proposedInterval === null ? <p>The start will be the moment you set this draft live. If one plan is live then, HealthCurve will end it at the same instant so the plans hand off without overlap.</p> : <>
            <p><RecordedPlanTime value={proposedInterval.effectiveFrom} /> through {proposedInterval.effectiveTo === null ? "ongoing" : <RecordedPlanTime value={proposedInterval.effectiveTo} />}</p>
            {expectedHandoff === undefined ? null : <p><strong>Expected handoff:</strong> the current plan will end when this plan starts. You do not need to adjust the old plan yourself.</p>}
            {blockingOverlaps.length === 0 ? <p>{expectedHandoff === undefined ? "No overlap with the plan versions shown in history." : "No other plan-date conflicts need your attention."}</p> : <OverlapList overlaps={blockingOverlaps} headingLevel={4} />}
          </>}
        </aside>
        <Textarea className="wide-field" label="Draft notes" name="notes" minRows={3} defaultValue={basis?.notes ?? ""} />
      </div>

      <fieldset><legend>Scheduled medication slots</legend>
        <p>These are scheduled plan entries—not records of medicine actually taken.</p>
        {slots.map((slot, index) => <div className="repeatable-row" key={index}>
          <label>Medication and formulation<select required value={slot.medication_id} onChange={(event) => { setSlots((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, medication_id: event.target.value } : item)); }}><option value="">Choose medication and formulation</option>{available.map((medication) => <option value={medication.id} key={medication.id}>{medicationOptionLabel(medication)}</option>)}</select></label>
          <label>Timing<select value={slot.timing_mode} onChange={(event) => { setSlots((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, timing_mode: event.target.value as SlotDraft["timing_mode"] } : item)); }}><option value="fixed_time">At a specific time</option><option value="wake">When I wake up</option></select></label>
          {slot.timing_mode === "fixed_time" ? (
            <label>Scheduled time<input type="time" required value={slot.scheduled_local_time} onChange={(event) => { setSlots((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, scheduled_local_time: event.target.value } : item)); }} /></label>
          ) : (
            <div className="field-group"><label htmlFor={`wake-reminder-time-${String(index)}`}>Reminder if unrecorded by</label><input id={`wake-reminder-time-${String(index)}`} type="time" required value={slot.reminder_local_time} onChange={(event) => { setSlots((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, reminder_local_time: event.target.value } : item)); }} /><small className="field-hint">The planned timing is “when I wake up.” This fallback only controls the reminder; it is not treated as the dose time.</small></div>
          )}
          <div className="field-group"><label htmlFor={`scheduled-dose-amount-${String(index)}`}>Scheduled dose amount</label><input id={`scheduled-dose-amount-${String(index)}`} aria-describedby={`scheduled-dose-help-${String(index)}`} type="number" required min="0.0001" step="any" inputMode="decimal" value={slot.amount} onChange={(event) => { setSlots((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, amount: event.target.value } : item)); }} /><small className="field-hint" id={`scheduled-dose-help-${String(index)}`}>This planned dose may differ from the formulation strength. Enter only the amount in the physician-approved plan.</small></div>
          <label>Scheduled dose unit<select value={slot.unit} onChange={(event) => { setSlots((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, unit: event.target.value as SlotDraft["unit"] } : item)); }}><option value="mg">mg</option><option value="mcg">mcg</option><option value="ml">mL</option><option value="tablet">tablet</option></select></label>
          <label>Route<select value={slot.route} onChange={(event) => { setSlots((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, route: event.target.value as SlotDraft["route"] } : item)); }}><option value="oral">Oral</option><option value="intramuscular">Intramuscular</option><option value="subcutaneous">Subcutaneous</option><option value="intravenous">Intravenous</option></select></label>
          <label>Condition (optional)<input maxLength={500} value={slot.condition} onChange={(event) => { setSlots((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, condition: event.target.value } : item)); }} /></label>
          <Button type="button" variant="outline" onClick={() => { setSlots((items) => items.filter((_, itemIndex) => itemIndex !== index)); }}>Remove slot</Button>
        </div>)}
        <Button type="button" variant="outline" onClick={() => { setSlots((items) => [...items, blankSlot(available[0]?.id)]); }}>Add scheduled slot</Button>
      </fieldset>

      <fieldset><legend>Physician-authored instructions</legend>
        <p>Copy only instructions actually supplied by a clinician. HealthCurve and its AI do not author or approve them.</p>
        {instructions.map((instruction, index) => <div className="repeatable-row" key={index}>
          <label>Category<select value={instruction.category} onChange={(event) => { setInstructions((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, category: event.target.value as InstructionDraft["category"] } : item)); }}><option value="general">General</option><option value="illness">Illness</option><option value="procedure">Procedure</option><option value="exercise">Exercise</option><option value="emergency">Emergency</option></select></label>
          <label>Title<input required maxLength={200} value={instruction.title} onChange={(event) => { setInstructions((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, title: event.target.value } : item)); }} /></label>
          <label className="wide-field">Instruction<textarea required value={instruction.body} onChange={(event) => { setInstructions((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, body: event.target.value } : item)); }} /></label>
          <label>Authored by<input required maxLength={200} value={instruction.authored_by} onChange={(event) => { setInstructions((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, authored_by: event.target.value } : item)); }} /></label>
          <label>Authored on<input type="date" required value={instruction.authored_on} onChange={(event) => { setInstructions((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, authored_on: event.target.value } : item)); }} /></label>
          <Button type="button" variant="outline" onClick={() => { setInstructions((items) => items.filter((_, itemIndex) => itemIndex !== index)); }}>Remove instruction</Button>
        </div>)}
        <Button type="button" variant="outline" onClick={() => { setInstructions((items) => [...items, blankInstruction()]); }}>Add physician instruction</Button>
      </fieldset>
      <Group className="form-actions"><Button type="submit" loading={mutation.isPending}>{editDraft === null ? "Save unapproved draft" : "Update unapproved draft"}</Button><Button type="button" variant="outline" onClick={onCancel}>Cancel</Button></Group>
      {mutation.isError ? <Alert color="red" role="alert"><strong>The draft was not saved.</strong> {mutation.error instanceof ApiError ? mutation.error.message : "Review the highlighted fields and try again."}</Alert> : null}
    </form>
  </section>;
}

function ApprovalForm({ version, activeVersion, focusOnMount, onComplete }: { version: RegimenVersion; activeVersion: RegimenVersion | null; focusOnMount: boolean; onComplete: (message: string) => void }): React.JSX.Element {
  const queryClient = useQueryClient();
  const headingRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => { if (focusOnMount) headingRef.current?.focus(); }, [focusOnMount]);
  const mutation = useMutation({ mutationFn: (payload: { approved_by: string; approval_source: string; approved_at: string | null; source_document_checksum: null; activation_local_time: string | null; activation_timezone: string; activation_fold: null }) => approveRegimen(version.id, payload), onSuccess: async () => { await invalidatePlans(queryClient); onComplete("Plan set live with physician approval recorded. Any single live predecessor was ended at this plan’s start; recorded doses were not changed."); } });
  const errorMessage = mutation.error instanceof ApiError ? mutation.error.message : "The plan could not be set live.";
  return <section className="approval-form" aria-labelledby={`approval-heading-${version.id}`}>
    <h4 id={`approval-heading-${version.id}`} ref={headingRef} tabIndex={-1}>Next step: review and set this plan live</h4>
    <p><strong>This draft is not active.</strong> Record approval here only after a physician has actually approved this plan. HealthCurve and its AI cannot approve it.</p>
    <p>Required provenance: the approving clinician or role and the source of approval, such as a consultation, letter, or portal message.</p>
    <div className="proposed-plan-interval" aria-live="polite"><h5>What will happen</h5><p>Choose the start below. If left blank, this plan starts when you press the set-live button.</p>{activeVersion === null ? <p>No plan is currently live.</p> : <p>HealthCurve will automatically end “{activeVersion.version_label}” at the new plan’s start. Its earlier history and all recorded doses remain unchanged.</p>}</div>
    <form className="aligned-form-grid plan-form-grid" onSubmit={(event) => { event.preventDefault(); const data = new FormData(event.currentTarget); mutation.mutate({ approved_by: formString(data, "approved_by"), approval_source: formString(data, "approval_source"), approved_at: formString(data, "approved_at") || null, source_document_checksum: null, activation_local_time: formString(data, "activation_local_time") || null, activation_timezone: version.effective_timezone ?? "UTC", activation_fold: null }); }}>
      <TextInput label="Approving clinician or role" aria-label="Approving clinician or role" name="approved_by" required maxLength={200} />
      <TextInput label="Approval source" aria-label="Approval source" name="approval_source" required maxLength={200} placeholder="Consultation, letter, or portal message" />
      <TextInput label="When the physician approved it (optional)" name="approved_at" type="datetime-local" />
      <TextInput
        className="wide-field"
        label="Start this plan (optional)"
        description={version.effective_from === null
          ? `Leave blank to start now. Times use ${version.effective_timezone ?? "your profile timezone"}.`
          : `The saved draft start is shown. Change it here if needed. Times use ${version.effective_timezone ?? "your profile timezone"}.`}
        name="activation_local_time"
        type="datetime-local"
        defaultValue={localInput(version.effective_from_local ?? version.effective_from)}
      />
      <Checkbox className="wide-field" required label="I confirm this records a real clinician-approved plan, not AI advice." />
      <Button type="submit" loading={mutation.isPending}>Set physician-approved plan live</Button>
      {mutation.isError ? <Alert color="red" role="alert">{errorMessage}</Alert> : null}
    </form>
  </section>;
}

function RetirementForm({ version, onComplete }: { version: RegimenVersion; onComplete: (message: string) => void }): React.JSX.Element {
  const queryClient = useQueryClient();
  const mutation = useMutation({ mutationFn: () => retireRegimen(version.id), onSuccess: async () => { await invalidatePlans(queryClient); onComplete("Plan version retired. Its history remains available."); } });
  return <details><summary>Retire this approved version</summary><p>Retirement ends an ongoing version now and preserves it as history. It does not delete recorded doses.</p><form onSubmit={(event) => { event.preventDefault(); mutation.mutate(); }}><label className="checkbox-label"><input type="checkbox" required /> I understand this will remove the version from active use.</label><button type="submit" disabled={mutation.isPending}>Retire and preserve history</button>{mutation.isError ? <p className="error-summary" role="alert">The plan version was not retired.</p> : null}</form></details>;
}

function PlanDeletionButton({ version, onDeleted }: { version: RegimenVersion; onDeleted: () => void }): React.JSX.Element {
  const queryClient = useQueryClient();
  const mutation = useMutation({ mutationFn: () => deleteRegimen(version.id), onSuccess: async () => { onDeleted(); await invalidatePlans(queryClient); } });
  const confirmDeletion = (): void => {
    const confirmed = window.confirm(`Permanently delete “${version.version_label}”?\n\nThis deletes the selected plan, its schedule slots, and its physician instructions. Recorded doses stay in HealthCurve but will no longer be linked to this plan. Saved reports remain frozen historical snapshots.`);
    if (confirmed) mutation.mutate();
  };
  return <div className="draft-delete danger-zone"><button type="button" className="danger-button" disabled={mutation.isPending} onClick={confirmDeletion}>Delete plan</button>{mutation.isError ? <p className="error-summary" role="alert">The plan was not deleted. This action is available only in the private development runtime.</p> : null}</div>;
}

export function PlanPage(): React.JSX.Element {
  const profileTimezone = useAuth().session?.user.defaultTimezone ?? "UTC";
  const [searchParams, setSearchParams] = useSearchParams();
  const appliedSearch = searchParams.toString();
  const view = useMemo(() => planHistoryView(appliedSearch, profileTimezone), [appliedSearch, profileTimezone]);
  const filters = useMemo<RecordedHistoryFilters>(() => ({ dateFrom: view.dateFrom, dateTo: view.dateTo, timezone: view.timezone }), [view.dateFrom, view.dateTo, view.timezone]);
  const [draftState, setDraftState] = useState({ search: appliedSearch, filters });
  const draft = draftState.search === appliedSearch ? draftState.filters : filters;
  const setDraft = (next: RecordedHistoryFilters): void => { setDraftState({ search: appliedSearch, filters: next }); };
  const invalidRange = filters.dateFrom !== "" && filters.dateTo !== "" && filters.dateFrom > filters.dateTo;
  const [validation, setValidation] = useState<string | null>(null);
  const active = useQuery({ queryKey: ["regimens", "active"], queryFn: getActiveRegimen });
  const history = useQuery({ queryKey: ["regimens", "history", filters, view.page], queryFn: () => getRegimens(filters, view.page), enabled: !invalidRange });
  const medications = useQuery({ queryKey: ["medications"], queryFn: getMedications });
  const [olderId, setOlderId] = useState("");
  const [newerId, setNewerId] = useState("");
  const [editor, setEditor] = useState<{ source: RegimenVersion | null; edit: RegimenVersion | null } | null>(null);
  const [message, setMessage] = useState("");
  const [reviewDraftId, setReviewDraftId] = useState<string | null>(null);
  const versions = history.data?.items ?? [];
  const chronological = [...versions].sort((a, b) => (a.effective_from === null ? 1 : b.effective_from === null ? -1 : a.effective_from.localeCompare(b.effective_from)));
  const selectedOlderId = olderId !== "" ? olderId : (chronological[chronological.length - 2]?.id ?? "");
  const selectedNewerId = newerId !== "" ? newerId : (chronological[chronological.length - 1]?.id ?? "");
  const diff = useQuery({ queryKey: ["regimen-diff", selectedOlderId, selectedNewerId], queryFn: () => getRegimenDiff(selectedOlderId, selectedNewerId), enabled: selectedOlderId !== "" && selectedNewerId !== "" && selectedOlderId !== selectedNewerId });
  const complete = (nextMessage: string): void => { setEditor(null); setReviewDraftId(null); setMessage(nextMessage); };
  const completeDraft = (nextMessage: string, version: RegimenVersion): void => {
    setEditor(null);
    if (view.page > 1) setSearchParams(planHistorySearch({ ...view, page: 1 }));
    setReviewDraftId(version.id);
    setMessage(`${nextMessage} Next, review it below and set it live only if it matches a plan your physician actually approved.`);
  };

  return <Page title="Medication plan" description="Physician-approved schedules and their provenance, kept separate from actual recorded doses.">
    {(active.isPending || history.isFetching || medications.isPending) ? <Text role="status">Loading medication plan…</Text> : null}
    {(active.isError || history.isError || medications.isError) ? <Alert color="red" role="alert">Medication plan data could not be loaded.</Alert> : null}
    {message === "" ? null : <Alert color="green" role="status">{message}</Alert>}
    {editor === null ? <Group className="page-actions"><Button type="button" onClick={() => { setMessage(""); setEditor({ source: active.data ?? null, edit: null }); }}>{active.data === null ? "Create first plan draft" : "Create new version from active plan"}</Button></Group> : null}
    {editor === null || medications.data === undefined ? null : <PlanEditor source={editor.source} editDraft={editor.edit} medications={medications.data} existingVersions={versions} timezone={profileTimezone} onCancel={() => { setEditor(null); }} onSaved={completeDraft} />}
    {active.data === null ? <Paper component="section" className="empty-state" withBorder radius="lg" p="lg"><Title order={2}>No approved plan currently in force</Title><Text mt="xs">Draft and historical versions appear below, but HealthCurve will not treat them as the active plan.</Text></Paper> : null}
    {active.data === undefined || active.data === null ? null : <PlanCard title={`${active.data.version_label} · currently in force`}><PlanContents version={active.data} timezone={profileTimezone} /></PlanCard>}

    <section aria-labelledby="history-heading"><Title order={2} id="history-heading">Version history</Title><Text>Approved and retired versions are immutable history. Edit a draft or create a new version to change a schedule.</Text>
      <PlanTimeline versions={versions} />
      <Paper component="form" className="filter-panel plan-history-filter" withBorder radius="lg" p="lg" onSubmit={(event) => { event.preventDefault(); if (draft.dateFrom !== "" && draft.dateTo !== "" && draft.dateFrom > draft.dateTo) { setValidation("From date must be on or before Through date."); return; } setValidation(null); setOlderId(""); setNewerId(""); setSearchParams(planHistorySearch({ ...draft, page: 1 })); }}><SimpleGrid cols={{ base: 1, sm: 3 }} className="form-wide"><TextInput label="Effective from date" type="date" value={draft.dateFrom} onChange={(event) => { setDraft({ ...draft, dateFrom: event.target.value }); }} /><TextInput label="Effective through date" type="date" value={draft.dateTo} onChange={(event) => { setDraft({ ...draft, dateTo: event.target.value }); }} /><TextInput label="History IANA timezone" aria-label="History IANA timezone" required value={draft.timezone} onChange={(event) => { setDraft({ ...draft, timezone: event.target.value }); }} /></SimpleGrid>{validation === null && !invalidRange ? null : <Alert color="red" className="form-wide" role="alert">{validation ?? "From date must be on or before Through date."}</Alert>}<Group className="filter-actions"><Button type="submit">Apply history filters</Button><Button variant="outline" type="button" onClick={() => { const reset = { dateFrom: "", dateTo: "", timezone: profileTimezone }; setValidation(null); setDraftState({ search: "", filters: reset }); setOlderId(""); setNewerId(""); setSearchParams(new URLSearchParams()); }}>Clear history filters</Button></Group></Paper>
      <Text c="dimmed" size="sm">Inclusive dates use {timezoneAbbreviation(filters.timezone)} and select versions by when the plan becomes effective. This does not change plan approval or active status.</Text>
      {history.data?.page.total_items === 0 ? <Text>No plan versions recorded.</Text> : null}
      {versions.length === 0 ? null : <Paper className="plan-history-region" withBorder radius="lg"><div className="table-scroll" tabIndex={0} role="region" aria-label="Medication plan version history table"><Table className="plan-history-table" verticalSpacing="sm" horizontalSpacing="md"><caption>Plan versions ordered by effective time, latest first; approval categories remain distinct.</caption><thead><tr><th scope="col">Effective period</th><th scope="col">Version</th><th scope="col">Approval state</th><th scope="col">Contents and actions</th></tr></thead><tbody>{versions.map((version) => <tr key={version.id}><td data-label="Effective period"><EffectivePeriod version={version} /></td><th data-label="Version" scope="row"><Title order={3}>{version.version_label}</Title></th><td data-label="Approval state"><span>{version.status === "draft" ? "Draft plan—not physician approved" : version.status === "approved" ? "Physician-approved" : "Retired"}</span>{version.status === "approved" ? <span>{version.approved_by ?? "Approval provenance missing"}</span> : null}</td><td data-label="Contents and actions"><details><summary>Show slots and instructions</summary><PlanContents version={version} timezone={profileTimezone} /></details>{version.status === "draft" ? <><Group className="form-actions"><Button type="button" variant="outline" onClick={() => { setMessage(""); setReviewDraftId(null); setEditor({ source: null, edit: version }); }}>Edit draft</Button></Group><ApprovalForm version={version} activeVersion={active.data ?? null} focusOnMount={reviewDraftId === version.id} onComplete={complete} /></> : null}{version.status === "approved" ? <RetirementForm version={version} onComplete={complete} /> : null}{version.deletion_allowed ? <PlanDeletionButton version={version} onDeleted={() => { if (view.page > 1 && versions.length === 1) setSearchParams(planHistorySearch({ ...view, page: view.page - 1 })); complete("The selected development plan was permanently deleted. Recorded doses were preserved without the deleted plan links."); }} /> : null}</td></tr>)}</tbody></Table></div></Paper>}
      {history.data === undefined ? null : <PaginationControls label="Plan version history" metadata={history.data.page} onPageChange={(page) => { setOlderId(""); setNewerId(""); setSearchParams(planHistorySearch({ ...view, page })); }} />}
    </section>

    <section aria-labelledby="diff-heading"><h2 id="diff-heading">Compare versions</h2>
      {history.data !== undefined && versions.length < 2 ? <p>At least two versions on this history page are needed for a comparison.</p> : <div className="diff-controls"><label>Older version<select value={selectedOlderId} onChange={(event) => { setOlderId(event.target.value); }}>{versions.map((version) => <option key={version.id} value={version.id}>{version.version_label}</option>)}</select></label><label>Newer version<select value={selectedNewerId} onChange={(event) => { setNewerId(event.target.value); }}>{versions.map((version) => <option key={version.id} value={version.id}>{version.version_label}</option>)}</select></label></div>}
      {selectedOlderId === selectedNewerId && selectedOlderId !== "" ? <p className="error-summary" role="alert">Choose two different versions.</p> : null}
      {diff.isPending && diff.isFetching ? <p role="status">Calculating deterministic version diff…</p> : null}
      {diff.isError ? <p className="error-summary" role="alert">The version comparison could not be loaded.</p> : null}
      {diff.data === undefined ? null : <div className="version-diff">{(["added", "removed", "changed"] as const).map((kind) => <section key={kind}><h3>{kind[0]?.toUpperCase()}{kind.slice(1)}</h3>{diff.data[kind]?.length === 0 ? <p>No {kind} schedule entries.</p> : <ul>{diff.data[kind]?.map((entry) => <li key={entry}>{formatQuantitativeText(entry)}</li>)}</ul>}</section>)}</div>}
    </section>
  </Page>;
}

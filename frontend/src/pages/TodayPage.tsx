import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import {
  getOpenEpisodes,
  getPlanComparison,
  recordDose,
  type PlanComparisonDay,
} from "../api/client";
import { useAuth } from "../auth/context";
import { FactCard, PlanCard } from "../components/CategoryCards";
import { Page } from "../components/Page";
import { formatDecimal, formatMeasurement } from "../format";
import { localDate, localDateTime } from "../time";

type Slot = PlanComparisonDay["slots"][number];

const statusLabels: Record<string, string> = {
  on_time: "Recorded on time",
  early: "Recorded early",
  late: "Recorded late",
  missing: "Not recorded",
  unplanned: "Recorded outside the schedule",
  extra: "Additional recorded dose",
};

function displayTime(value: string | null): string {
  if (value === null) return "Time not recorded";
  const time = value.includes("T") ? value.split("T")[1] : value;
  return time?.slice(0, 5) ?? value;
}

function SlotRow({ slot, timezone, day }: { slot: Slot; timezone: string; day: string }): React.JSX.Element {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => {
      if (slot.slot_id === null || slot.planned_amount === null) {
        throw new Error("Only an approved-plan slot can be recorded with this action.");
      }
      return recordDose({
        medication_id: slot.medication_id,
        amount: slot.planned_amount,
        unit: slot.unit,
        route: slot.route,
        category: "scheduled",
        slot_id: slot.slot_id,
        time: { local_time: localDateTime(new Date(), timezone), timezone },
      });
    },
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["today", day, timezone] }),
  });
  const label = statusLabels[slot.status] ?? slot.status;

  return (
    <li className={`dose-slot dose-slot--${slot.status}`}>
      <div>
        <p className="dose-slot__schedule">
          <strong>{displayTime(slot.scheduled_local_time)}</strong> · {slot.medication_name} · {formatMeasurement(slot.planned_amount, slot.unit)}
        </p>
        <p className="dose-slot__status"><strong>{label}</strong></p>
        {slot.status === "missing" ? (
          <p className="status-explanation">No dose record exists for this slot. “Not recorded” does not mean “not taken.”</p>
        ) : (
          <p className="status-explanation">
            Recorded fact: {formatMeasurement(slot.actual_amount, slot.unit)} at {displayTime(slot.actual_local_time)}.
          </p>
        )}
      </div>
      {slot.status === "missing" && slot.planned_amount !== null ? (
        <button type="button" disabled={mutation.isPending} onClick={() => { mutation.mutate(); }}>
          {mutation.isPending ? "Recording…" : `Record ${formatMeasurement(slot.planned_amount, slot.unit)} taken now`}
        </button>
      ) : null}
      {mutation.isError ? <p className="error-summary" role="alert">The dose was not recorded. Review the time and try again.</p> : null}
      {mutation.isSuccess ? <p className="success-message" role="status">Dose recorded as a fact.</p> : null}
    </li>
  );
}

export function TodayPage(): React.JSX.Element {
  const { session } = useAuth();
  const timezone = session?.user.defaultTimezone ?? "UTC";
  const day = localDate(new Date(), timezone);
  const comparison = useQuery({
    queryKey: ["today", day, timezone],
    queryFn: () => getPlanComparison(day, timezone),
  });
  const episodes = useQuery({ queryKey: ["open-episodes"], queryFn: getOpenEpisodes });

  const planSlots = comparison.data?.slots.filter((slot) => slot.slot_id !== null) ?? [];
  const unplannedDoses = comparison.data?.slots.filter((slot) => slot.slot_id === null) ?? [];
  const planVersions = comparison.data?.regimen_versions ?? [];
  const hasRecordedDose = comparison.data?.slots.some((slot) => slot.dose_id !== null) ?? false;
  const openEpisode = episodes.data?.items[0];

  return (
    <Page title="Today" description="Recorded facts and your physician-approved plan remain separate.">
      <p className="today-date"><strong>{day}</strong> · {timezone}</p>
      <div className="quick-actions" aria-label="Quick actions">
        <Link className="button-link" to="/timeline">Open timeline</Link>
        <Link className="button-link" to="/doses">Review doses</Link>
        <a className="button-link button-link--urgent" href="/emergency">Open emergency plan</a>
      </div>

      {comparison.isPending ? <p role="status">Loading today’s record…</p> : null}
      {comparison.isError ? <p className="error-summary" role="alert">Today’s dose record could not be loaded.</p> : null}

      {comparison.data !== undefined && planVersions.length === 0 ? (
        <section className="empty-state" aria-labelledby="no-plan">
          <h2 id="no-plan">No approved plan for this date</h2>
          <p>Recorded doses still appear as facts. HealthCurve will not infer a schedule from them.</p>
          <Link to="/plan">Review plan history</Link>
        </section>
      ) : null}

      {comparison.data !== undefined && planVersions.length > 0 ? (
        <PlanCard title={planVersions.length === 1 ? planVersions[0]?.version_label ?? "Approved regimen" : `${formatDecimal(planVersions.length)} approved plan periods`} metadata={<Link to="/plan">Review approved plan</Link>}>
          <p>Schedule for {day} in {timezone}. {planVersions.length > 1 ? "The physician-approved plan changed during this day; each slot is tied to its historical plan period. " : ""}A missing record is not proof that a dose was not taken.</p>
          {planSlots.length === 0 ? <p>No scheduled slots are recorded in this approved version.</p> : (
            <ol className="dose-slots">
              {planSlots.map((slot) => <SlotRow key={slot.slot_id} slot={slot} timezone={timezone} day={day} />)}
            </ol>
          )}
          <details className="metric-definition">
            <summary>How timing status is calculated</summary>
            <p>{comparison.data.metric_definition}</p>
          </details>
        </PlanCard>
      ) : null}

      {comparison.data !== undefined && !hasRecordedDose ? (
        <section className="empty-state" aria-labelledby="no-doses">
          <h2 id="no-doses">No doses recorded today</h2>
          <p>This is an empty record, not a recorded amount of zero.</p>
        </section>
      ) : null}

      {unplannedDoses.map((dose) => (
        <FactCard key={dose.dose_id} title={`${dose.medication_name} recorded`} metadata={<Link to="/timeline">Open recorded fact</Link>}>
          <p>{formatMeasurement(dose.actual_amount, dose.unit)} at {displayTime(dose.actual_local_time)} · {statusLabels[dose.status] ?? dose.status}</p>
        </FactCard>
      ))}

      {episodes.isError ? <p className="error-summary" role="alert">Open-episode status could not be loaded.</p> : null}
      {openEpisode === undefined ? null : (
        <FactCard title="Open stress episode" metadata={<Link to="/episodes">Review episode</Link>}>
          <p><strong>Trigger recorded:</strong> {openEpisode.trigger}</p>
          <p>Started {openEpisode.started_at} · {openEpisode.timezone}</p>
          <p>{formatDecimal(openEpisode.dose_count)} linked dose record(s) · {formatDecimal(openEpisode.symptom_count)} linked symptom record(s)</p>
        </FactCard>
      )}
    </Page>
  );
}

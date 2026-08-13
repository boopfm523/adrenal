import { Alert, Anchor, Button, Group, Paper, Stack, Table, Text, Title } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";

import {
  getOpenEpisodes,
  getPlanComparison,
  recordDose,
  type PlanComparisonDay,
} from "../api/client";
import { useAuth } from "../auth/context";
import { FactCard, PlanCard } from "../components/CategoryCards";
import { Page } from "../components/Page";
import { PaginationControls } from "../components/PaginationControls";
import { formatDecimal, formatMeasurement } from "../format";
import { localDate, localDateTime, timezoneAbbreviation, timezoneAbbreviationForLocalDate } from "../time";

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

function positivePage(value: string | null): number {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : 1;
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
    <tr className={`dose-slot--${slot.status}`}>
      <td data-label="Scheduled time"><strong>{displayTime(slot.scheduled_local_time)}</strong></td>
      <th data-label="Medication" scope="row">{slot.medication_name}<span>{formatMeasurement(slot.planned_amount, slot.unit)}</span></th>
      <td data-label="Status"><Stack gap="xs">
        <Text className="dose-slot__schedule">
          <strong>{label}</strong>
        </Text>
        {slot.status === "missing" ? (
          <Text className="status-explanation">No dose record exists for this slot. “Not recorded” does not mean “not taken.”</Text>
        ) : (
          <Text className="status-explanation">
            Recorded fact: {formatMeasurement(slot.actual_amount, slot.unit)} at {displayTime(slot.actual_local_time)}.
          </Text>
        )}
      </Stack></td>
      <td data-label="Action">{slot.status === "missing" && slot.planned_amount !== null ? (
        <Button type="button" loading={mutation.isPending} onClick={() => { mutation.mutate(); }}>
          {mutation.isPending ? "Recording…" : `Record ${formatMeasurement(slot.planned_amount, slot.unit)} taken now`}
        </Button>
      ) : null}
      {mutation.isError ? <Alert color="red" role="alert">The dose was not recorded. Review the time and try again.</Alert> : null}
      {mutation.isSuccess ? <Alert color="green" role="status">Dose recorded as a fact.</Alert> : null}</td>
    </tr>
  );
}

export function TodayPage(): React.JSX.Element {
  const { session } = useAuth();
  const timezone = session?.user.defaultTimezone ?? "UTC";
  const day = localDate(new Date(), timezone);
  const [searchParams, setSearchParams] = useSearchParams();
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
  const healthCurveUrl = `/healthcurve?${new URLSearchParams({ day, timezone }).toString()}`;
  const pageSize = 10;
  const planPage = positivePage(searchParams.get("plan_page"));
  const recordedPage = positivePage(searchParams.get("recorded_page"));
  const setPage = (key: "plan_page" | "recorded_page", page: number): void => {
    const next = new URLSearchParams(searchParams);
    if (page === 1) next.delete(key);
    else next.set(key, page.toString());
    setSearchParams(next);
  };
  const visiblePlanSlots = planSlots.slice((planPage - 1) * pageSize, planPage * pageSize);
  const visibleRecordedDoses = unplannedDoses.slice((recordedPage - 1) * pageSize, recordedPage * pageSize);

  return (
    <Page title="Today" description="Recorded facts and your physician-approved plan remain separate.">
      <Text className="today-date"><strong>{day}</strong> · {timezoneAbbreviationForLocalDate(timezone, day)}</Text>
      <Paper component="section" className="primary-healthcurve-entry" withBorder radius="lg" p={{ base: "md", sm: "xl" }} aria-labelledby="today-healthcurve-title">
        <Title order={2} id="today-healthcurve-title">Review today’s HealthCurve</Title>
        <Text mt="xs">See actual recorded dose timing beside stress, symptoms, Garmin observations, and vital signs without entering anything twice.</Text>
        <Button component={Link} to={healthCurveUrl} mt="md">Open today’s HealthCurve</Button>
      </Paper>
      <Group className="quick-actions" aria-label="Quick actions">
        <Button component={Link} variant="outline" to="/timeline">Open timeline</Button>
        <Button component={Link} variant="outline" to="/doses">Review doses</Button>
        <Button component="a" variant="outline" color="red" href="/emergency">Open emergency plan</Button>
      </Group>

      {comparison.isPending ? <Text role="status">Loading today’s record…</Text> : null}
      {comparison.isError ? <Alert color="red" role="alert">Today’s dose record could not be loaded.</Alert> : null}

      {comparison.data !== undefined && planVersions.length === 0 ? (
        <Paper component="section" className="empty-state" withBorder radius="lg" p="lg" aria-labelledby="no-plan">
          <Title order={2} id="no-plan">No approved plan for this date</Title>
          <Text mt="xs">Recorded doses still appear as facts. HealthCurve will not infer a schedule from them.</Text>
          <Anchor component={Link} to="/plan">Review plan history</Anchor>
        </Paper>
      ) : null}

      {comparison.data !== undefined && planVersions.length > 0 ? (
        <PlanCard title={planVersions.length === 1 ? planVersions[0]?.version_label ?? "Approved regimen" : `${formatDecimal(planVersions.length)} approved plan periods`} metadata={<Link to="/plan">Review approved plan</Link>}>
          <p>Schedule for {day} in {timezoneAbbreviationForLocalDate(timezone, day)}. {planVersions.length > 1 ? "The physician-approved plan changed during this day; each slot is tied to its historical plan period. " : ""}A missing record is not proof that a dose was not taken.</p>
          {planSlots.length === 0 ? <p>No scheduled slots are recorded in this approved version.</p> : <><div className="table-scroll today-table-region today-table-region--plan" tabIndex={0} role="region" aria-label="Today's approved schedule and recorded status"><Table className="today-table" verticalSpacing="sm" horizontalSpacing="md"><caption>Approved schedule and corresponding recorded facts for today.</caption><thead><tr><th scope="col">Scheduled time</th><th scope="col">Medication</th><th scope="col">Status</th><th scope="col">Action</th></tr></thead><tbody>{visiblePlanSlots.map((slot) => <SlotRow key={slot.slot_id} slot={slot} timezone={timezone} day={day} />)}</tbody></Table></div><PaginationControls label="Today's approved schedule" metadata={{ page: planPage, page_size: pageSize, total_items: planSlots.length, total_pages: Math.ceil(planSlots.length / pageSize) }} onPageChange={(page) => { setPage("plan_page", page); }} /></>}
          <details className="metric-definition">
            <summary>How timing status is calculated</summary>
            <p>{comparison.data.metric_definition}</p>
          </details>
        </PlanCard>
      ) : null}

      {comparison.data !== undefined && !hasRecordedDose ? (
        <Paper component="section" className="empty-state" withBorder radius="lg" p="lg" aria-labelledby="no-doses">
          <Title order={2} id="no-doses">No doses recorded today</Title>
          <Text mt="xs">This is an empty record, not a recorded amount of zero.</Text>
        </Paper>
      ) : null}

      {unplannedDoses.length === 0 ? null : <FactCard title="Additional recorded doses" metadata={<Link to="/timeline">Open recorded facts</Link>}><div className="table-scroll today-table-region today-table-region--fact" tabIndex={0} role="region" aria-label="Today's additional recorded doses"><Table className="today-table" verticalSpacing="sm" horizontalSpacing="md"><caption>Recorded dose facts that do not correspond to an approved-plan slot.</caption><thead><tr><th scope="col">Recorded time</th><th scope="col">Medication</th><th scope="col">Amount</th><th scope="col">Status</th></tr></thead><tbody>{visibleRecordedDoses.map((dose) => <tr key={dose.dose_id}><td data-label="Recorded time">{displayTime(dose.actual_local_time)}</td><th data-label="Medication" scope="row">{dose.medication_name}</th><td data-label="Amount">{formatMeasurement(dose.actual_amount, dose.unit)}</td><td data-label="Status">{statusLabels[dose.status] ?? dose.status}</td></tr>)}</tbody></Table></div><PaginationControls label="Today's additional recorded doses" metadata={{ page: recordedPage, page_size: pageSize, total_items: unplannedDoses.length, total_pages: Math.ceil(unplannedDoses.length / pageSize) }} onPageChange={(page) => { setPage("recorded_page", page); }} /></FactCard>}

      {episodes.isError ? <Alert color="red" role="alert">Open-episode status could not be loaded.</Alert> : null}
      {openEpisode === undefined ? null : (
        <FactCard title="Open stress episode" metadata={<Link to="/episodes">Review episode</Link>}>
          <p><strong>Trigger recorded:</strong> {openEpisode.trigger}</p>
          <p>Started {openEpisode.started_at} · {timezoneAbbreviation(openEpisode.timezone, openEpisode.started_at)}</p>
          <p>{formatDecimal(openEpisode.dose_count)} linked dose record(s) · {formatDecimal(openEpisode.symptom_count)} linked symptom record(s)</p>
        </FactCard>
      )}
    </Page>
  );
}

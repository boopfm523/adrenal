import { Alert, Anchor, Button, Group, NativeSelect, Paper, SimpleGrid, Text, TextInput, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import {
  getHealthRecord,
  getHealthRecordBatches,
  getHealthRecordOrganizations,
  getHealthRecords,
  healthRecordFileUrl,
  type HealthRecord,
  type HealthRecordFilters,
  type HealthRecordState,
  type HealthRecordTable,
} from "../api/client";
import { Page } from "../components/Page";
import { PaginationControls } from "../components/PaginationControls";

const TABLES: readonly (readonly [HealthRecordTable, string])[] = [
  ["imaging_studies", "Imaging"],
  ["encounters", "Visits"],
  ["documents", "Documents"],
  ["conditions", "Conditions"],
  ["immunizations", "Immunizations"],
  ["allergies", "Allergies"],
  ["vitals", "Portal vitals"],
  ["surgical_history", "Surgical history"],
  ["medications", "Medication lists"],
] as const;

const DEFAULT_TABLE: HealthRecordTable = "imaging_studies";

interface View extends HealthRecordFilters {
  page: number;
}

function readView(search: string): View {
  const params = new URLSearchParams(search);
  const table = TABLES.find(([value]) => value === params.get("table"))?.[0] ?? DEFAULT_TABLE;
  const state = params.get("state");
  const page = Number.parseInt(params.get("page") ?? "1", 10);
  return {
    table,
    state: state === "present" || state === "no_longer_in_source" ? state : undefined,
    dateFrom: params.get("date_from") ?? "",
    dateTo: params.get("date_to") ?? "",
    page: Number.isFinite(page) && page > 0 ? page : 1,
  };
}

function writeView(view: View): URLSearchParams {
  const params = new URLSearchParams({ table: view.table });
  if (view.state) params.set("state", view.state);
  if (view.dateFrom) params.set("date_from", view.dateFrom);
  if (view.dateTo) params.set("date_to", view.dateTo);
  if (view.page > 1) params.set("page", String(view.page));
  return params;
}

function recordDate(record: HealthRecord): string {
  if (record.local_time != null) {
    const zone = record.timezone == null ? "" : ` (${record.timezone}${record.timezone_assumed ? ", zone assumed" : ""})`;
    return `${record.local_time.replace("T", " ").slice(0, 16)}${zone}`;
  }
  return record.record_date ?? record.date_text ?? "Date not given";
}

function clinicians(record: HealthRecord): string {
  const people = [
    record.provider == null ? null : `Clinician: ${record.provider}`,
    record.ordered_by == null ? null : `Ordered by: ${record.ordered_by}`,
    record.reading_provider == null ? null : `Read by: ${record.reading_provider}`,
    record.performing_lab == null ? null : `Lab: ${record.performing_lab}`,
    record.prescriber == null ? null : `Prescriber: ${record.prescriber}`,
  ].filter((value): value is string => value !== null);
  return people.length === 0 ? "Not recorded" : people.join("; ");
}

const PREVIEW_CHARACTERS = 160;

function preview(text: string): string {
  const flat = text.replace(/\s+/gu, " ").trim();
  return flat.length <= PREVIEW_CHARACTERS ? flat : `${flat.slice(0, PREVIEW_CHARACTERS - 1)}… (open the record for the rest)`;
}

function describe(record: HealthRecord): string {
  return [record.category_label, record.kind].filter((part): part is string => part != null && part !== "").join(" · ");
}

function RecordDetail({ recordId, close }: { recordId: string; close: () => void }): React.JSX.Element {
  const detail = useQuery({ queryKey: ["health-record", recordId], queryFn: () => getHealthRecord(recordId) });
  const heading = useRef<HTMLHeadingElement>(null);
  const loaded = detail.data?.id;
  // The detail opens above the list, so move the reader to it rather than leaving the
  // click with no visible effect.
  useEffect(() => {
    if (loaded === undefined) return;
    heading.current?.scrollIntoView({ block: "start" });
    heading.current?.focus();
  }, [loaded]);
  if (detail.isError) return <Alert color="red" role="alert">This record could not be loaded.</Alert>;
  if (detail.data === undefined) return <Text role="status">Loading record…</Text>;
  const record = detail.data;
  return (
    <Paper component="section" withBorder radius="lg" p="lg" my="lg" aria-labelledby="health-record-detail-heading">
      <Group justify="space-between">
        <Title order={2} id="health-record-detail-heading" ref={heading} tabIndex={-1}>{record.title}</Title>
        <Button type="button" variant="outline" onClick={close}>Close record</Button>
      </Group>
      <dl className="record-provenance">
        <dt>From</dt><dd>{record.organization}</dd>
        <dt>Date</dt><dd>{recordDate(record)}</dd>
        <dt>People</dt><dd>{clinicians(record)}</dd>
        <dt>Received through</dt><dd>{record.source}</dd>
        <dt>Source record</dt><dd><code>{record.record_id}</code>{record.state === "no_longer_in_source" ? " · no longer in source" : ""}</dd>
      </dl>
      {record.summary_text == null ? null : <><Title order={3}>Summary</Title><p className="record-text">{record.summary_text}</p></>}
      {record.body_text == null ? null : <><Title order={3}>Full text</Title><pre className="record-text" tabIndex={0}>{record.body_text}</pre></>}
      {record.file_paths.length === 0 ? null : (
        <>
          <Title order={3}>Original files</Title>
          <ul>
            {record.file_paths.map((path, index) => <li key={path}><a href={healthRecordFileUrl(record.id, index)} download>Download {path.split("/").at(-1) ?? path}</a></li>)}
          </ul>
        </>
      )}
      <Title order={3}>Versions</Title>
      <ul>
        {record.revisions.map((revision) => <li key={revision.revision}>Version {revision.revision}, imported {revision.created_at.replace("T", " ").slice(0, 16)} UTC</li>)}
      </ul>
    </Paper>
  );
}

function LatestImport(): React.JSX.Element | null {
  const batches = useQuery({ queryKey: ["health-record-batches"], queryFn: () => getHealthRecordBatches() });
  const latest = batches.data?.items[0];
  if (latest === undefined) return batches.isFetched ? <Alert color="blue" mb="lg">No health records have been imported yet. See Help for how to run the import.</Alert> : null;
  const exported = Object.entries(latest.source_exported_at).map(([source, date]) => `${source} on ${date}`).join("; ");
  return (
    <Text c="dimmed" mb="md">
      Last import {latest.completed_at.replace("T", " ").slice(0, 16)} UTC from a dataset generated {latest.dataset_generated_at}. Sources last pulled: {exported || "not stated"}.
    </Text>
  );
}

export function HealthRecordsPage(): React.JSX.Element {
  const [searchParams, setSearchParams] = useSearchParams();
  const applied = searchParams.toString();
  // Which health system a record came from is health information. The filter holds an
  // opaque per-owner key, kept in page state: the name never enters the address bar,
  // the API URL, or an access log.
  const [organization, setOrganization] = useState("");
  const urlView = useMemo(() => readView(applied), [applied]);
  const view = useMemo<View>(() => ({ ...urlView, organizationKey: organization }), [urlView, organization]);
  const [selected, setSelected] = useState<string | null>(null);
  const records = useQuery({
    queryKey: ["health-records", view],
    queryFn: () => getHealthRecords(view, view.page),
  });
  const organizations = useQuery({ queryKey: ["health-record-organizations"], queryFn: getHealthRecordOrganizations });
  const label = TABLES.find(([value]) => value === view.table)?.[1] ?? "Records";
  const items = records.data?.items ?? [];

  function apply(event: React.SyntheticEvent<HTMLFormElement>): void {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const state = data.get("state") as string;
    setSelected(null);
    setOrganization(data.get("from") as string);
    setSearchParams(writeView({
      table: data.get("table") as HealthRecordTable,
      state: state === "" ? undefined : state as HealthRecordState,
      dateFrom: data.get("date_from") as string,
      dateTo: data.get("date_to") as string,
      page: 1,
    }));
  }

  return (
    <Page title="Health records" description="Records imported read-only from your patient portals, each shown with the organization it came from. They are reference history: nothing here is your medication plan or a recorded dose.">
      <Alert color="orange" mb="lg">
        <strong>Hidden from chat and AI by default.</strong> Choose which categories chat may read in <Link to="/settings">Settings &amp; privacy</Link>. Lab results from these sources are on <Link to="/labs">Labs</Link>.
      </Alert>
      <LatestImport />
      <Paper component="form" withBorder p="lg" my="xl" radius="lg" onSubmit={apply} aria-labelledby="health-record-filter-heading">
        <Title order={2} mb="md" id="health-record-filter-heading">Filter health records</Title>
        <SimpleGrid cols={{ base: 1, sm: 3 }}>
          <NativeSelect label="Record type" name="table" defaultValue={view.table} key={`table-${view.table}`} data={TABLES.map(([value, text]) => ({ value, label: text }))} />
          <NativeSelect label="From" name="from" defaultValue={organization} key={`from-${organization}-${String(organizations.data?.length ?? 0)}`} data={[{ value: "", label: "Any organization" }, ...(organizations.data ?? []).map((item) => ({ value: item.key, label: item.name }))]} />
          <NativeSelect label="Status" name="state" defaultValue={view.state ?? ""} key={`state-${view.state ?? ""}`} data={[{ value: "", label: "Any" }, { value: "present", label: "In the latest import" }, { value: "no_longer_in_source", label: "No longer in source" }]} />
          <TextInput label="Source date from" type="date" name="date_from" defaultValue={view.dateFrom} key={`df-${view.dateFrom ?? ""}`} />
          <TextInput label="Through" type="date" name="date_to" defaultValue={view.dateTo} key={`dt-${view.dateTo ?? ""}`} />
        </SimpleGrid>
        <Group mt="md">
          <Button type="submit">Show records</Button>
          <Button variant="outline" type="button" onClick={() => { setSelected(null); setOrganization(""); setSearchParams(writeView({ table: view.table, page: 1 })); }}>Clear filters</Button>
        </Group>
        <Text c="dimmed" mt="sm">Dates are the source&apos;s own calendar dates.</Text>
      </Paper>
      {view.table === "medications" ? (
        <Alert color="gray" mb="lg" role="note">
          <strong>Medication lists as each organization recorded them.</strong> Reference history only: these are not your plan or your doses, never become either, and are never shown to chat or AI.
        </Alert>
      ) : null}
      {selected === null ? null : <RecordDetail recordId={selected} close={() => { setSelected(null); }} />}
      {records.isFetching ? <Text role="status">Loading records…</Text> : null}
      {records.isError ? <Alert color="red" role="alert">Health records could not be loaded.</Alert> : null}
      {!records.isFetching && !records.isError && items.length === 0 ? <p>No {label.toLowerCase()} match these filters.</p> : null}
      {items.length === 0 ? null : (
        <section aria-labelledby="health-records-heading">
          <h2 id="health-records-heading">{label}</h2>
          <div className="standard-table-region health-record-table-region">
          <div className="table-scroll" tabIndex={0} role="region" aria-label={`${label} records`}>
            <table className="health-record-table">
              <thead><tr><th scope="col">Date</th><th scope="col">Record</th><th scope="col">From</th><th scope="col">People</th><th scope="col">Details</th><th scope="col">Status</th></tr></thead>
              <tbody>
                {items.map((record) => (
                  <tr key={record.id}>
                    <td data-label="Date">{recordDate(record)}</td>
                    <th scope="row" data-label="Record">
                      <span>
                        <Anchor component="button" type="button" className="health-record-open" onClick={() => { setSelected(record.id); }}>{record.title}</Anchor>
                        {describe(record) === "" ? null : <><br />{describe(record)}</>}
                      </span>
                    </th>
                    <td data-label="From">{record.organization}</td>
                    <td data-label="People">{clinicians(record)}</td>
                    <td data-label="Details">{record.summary_text == null ? (record.has_full_text ? "Full text available" : "—") : preview(record.summary_text)}</td>
                    <td data-label="Status">{record.state === "no_longer_in_source" ? <strong>No longer in source</strong> : "Current"}{record.revision > 1 ? ` · version ${String(record.revision)}` : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          </div>
          {records.data === undefined ? null : <PaginationControls label={`${label} records`} metadata={records.data.page} onPageChange={(page) => { setSelected(null); setSearchParams(writeView({ ...view, page })); }} />}
        </section>
      )}
    </Page>
  );
}

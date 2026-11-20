import { Alert, Button, Group, Text, Title } from "@mantine/core";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { ApiError, runHealthRecordImport, type HealthRecordImportRun } from "../api/client";

const TABLE_LABELS: Record<string, string> = {
  lab_results: "Lab results",
  imaging_studies: "Imaging",
  documents: "Documents",
  encounters: "Visits",
  medications: "Medication lists",
  conditions: "Conditions",
  immunizations: "Immunizations",
  allergies: "Allergies",
  vitals: "Portal vitals",
  surgical_history: "Surgical history",
};

function failure(error: unknown): string {
  if (error instanceof ApiError && error.message !== "") return error.message;
  return "The import could not run. Nothing was written.";
}

function RunSummary({ run }: { run: HealthRecordImportRun }): React.JSX.Element {
  const changed = run.tables.reduce((sum, row) => sum + row.new + row.changed + row.no_longer_in_source + row.returned, 0);
  return (
    <div className="health-record-import-result" role="status" aria-live="polite">
      <Text mt="md">
        {run.dry_run
          ? changed === 0 ? "Nothing has changed since the last import." : `${String(changed)} record change(s) would be imported. Nothing was written.`
          : `Imported. ${String(run.lab_panels_created)} lab panel(s) added and ${String(run.lab_panels_corrected)} corrected.`}
        {" "}Dataset generated {run.dataset_generated_at}.
      </Text>
      <div className="table-scroll" tabIndex={0} role="region" aria-label={run.dry_run ? "Changes an import would make" : "Changes imported"}>
        <table>
          <thead><tr><th scope="col">Record type</th><th scope="col">New</th><th scope="col">Changed</th><th scope="col">Same</th><th scope="col">No longer in source</th><th scope="col">Back in source</th></tr></thead>
          <tbody>
            {run.tables.map((row) => (
              <tr key={row.table}>
                <th scope="row">{TABLE_LABELS[row.table] ?? row.table}</th>
                <td>{row.new}</td><td>{row.changed}</td><td>{row.unchanged}</td><td>{row.no_longer_in_source}</td><td>{row.returned}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Check the read-only portal dataset for changes, or import it now (ADR-0040). */
export function HealthRecordImportPanel({ headingLevel = 2 }: { headingLevel?: 2 | 3 }): React.JSX.Element {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: (dryRun: boolean) => runHealthRecordImport(dryRun),
    onSuccess: async (run) => {
      if (run.dry_run) return;
      await Promise.all(
        ["health-records", "health-record-batches", "health-record-organizations", "health-record", "lab-results", "lab-tests", "lab-test-history"].map((key) =>
          queryClient.invalidateQueries({ queryKey: [key] }),
        ),
      );
    },
  });
  return (
    <section aria-labelledby="health-record-import-heading" className="settings-card">
      <Title order={headingLevel} id="health-record-import-heading">Import from your patient portals</Title>
      <Text mt="xs">
        Reads the health-records dataset (read-only) and adds what changed. Unchanged records stay as they are, corrections keep the earlier version, and records the portals no longer have are marked, never deleted.
      </Text>
      <Group mt="md">
        <Button type="button" variant="outline" loading={mutation.isPending && mutation.variables} disabled={mutation.isPending} onClick={() => { mutation.mutate(true); }}>Check for changes</Button>
        <Button type="button" loading={mutation.isPending && !mutation.variables} disabled={mutation.isPending} onClick={() => { mutation.mutate(false); }}>Import now</Button>
      </Group>
      {mutation.isError ? <Alert color="red" role="alert" mt="md">{failure(mutation.error)}</Alert> : null}
      {mutation.data === undefined ? null : <RunSummary run={mutation.data} />}
    </section>
  );
}

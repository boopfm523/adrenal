import { useQuery } from "@tanstack/react-query";

import { getLabResults, type LabResult } from "../api/client";
import { useAuth } from "../auth/context";
import { AccessibleLineChart } from "../components/AccessibleLineChart";
import { Page } from "../components/Page";

interface TrendGroup {
  key: string;
  name: string;
  specimen: string;
  unit: string;
  values: LabResult[];
}

function displayedSourceValue(result: LabResult): string {
  if (result.original_value !== null) return `${result.original_value}${result.original_unit === null ? "" : ` ${result.original_unit}`}`;
  return result.qualitative_result ?? "Missing source value";
}

function specimenLabel(result: LabResult): string {
  const specimen = result.specimen_type?.trim();
  return specimen === undefined || specimen === "" ? "Specimen type not recorded" : specimen;
}

function trendGroups(results: LabResult[]): TrendGroup[] {
  const groups = new Map<string, TrendGroup>();
  results.forEach((result) => {
    if (result.normalized_analyte_code === null || result.normalized_value === null || result.normalized_unit === null) return;
    const specimen = specimenLabel(result);
    const key = [result.normalized_analyte_code, specimen.toLocaleLowerCase(), result.normalized_unit].join("|");
    const existing = groups.get(key);
    if (existing === undefined) {
      groups.set(key, { key, name: result.normalized_analyte_name ?? result.normalized_analyte_code, specimen, unit: result.normalized_unit, values: [result] });
    } else {
      existing.values.push(result);
    }
  });
  return [...groups.values()]
    .map((group) => ({ ...group, values: [...group.values].sort((a, b) => a.specimen_time.occurred_at.localeCompare(b.specimen_time.occurred_at)) }))
    .sort((a, b) => a.name.localeCompare(b.name));
}

function dateLabel(result: LabResult): string {
  return `${result.specimen_time.local_time.replace("T", " ")} ${result.specimen_time.timezone}`;
}

export function LabsPage(): React.JSX.Element {
  const timezone = useAuth().session?.user.defaultTimezone ?? "UTC";
  const query = useQuery({ queryKey: ["lab-results"], queryFn: getLabResults });
  const results = query.data ?? [];
  const groups = trendGroups(results);
  return <Page title="Laboratory results" description="Original report values remain recorded facts. HealthCurve shows optional deterministic derived values separately; it does not diagnose, interpret cortisol, or recommend treatment.">
    <aside className="safety-note"><strong>Descriptive records only.</strong> Reference ranges are preserved exactly from each source and are never invented or used here to diagnose. Cortisol collection time and specimen type materially affect context; discuss interpretation with your physician.</aside>
    {query.isPending ? <p role="status">Loading laboratory facts…</p> : null}
    {query.isError ? <p role="alert" className="error-summary">Laboratory facts could not be loaded.</p> : null}
    {!query.isPending && !query.isError && results.length === 0 ? <p>No laboratory facts recorded.</p> : null}
    {groups.map((group) => <AccessibleLineChart key={group.key} title={`${group.name} — ${group.specimen}`} summary="Each point is one recorded specimen. Lines are descriptive only; missing intervals are not inferred." unit={group.unit} timezone={timezone} dateRange={`${group.values[0]?.specimen_time.local_time.slice(0, 10) ?? "Unavailable"} through ${group.values.at(-1)?.specimen_time.local_time.slice(0, 10) ?? "Unavailable"}`} definition={`Values use ${group.values[0]?.normalization_method ?? "the recorded deterministic normalization rule"}. Results are grouped only when canonical analyte, specimen type, and normalized unit match.`} sampleCount={group.values.length} missingCount={0} series={[{ name: group.name, source: "recorded lab facts with deterministic derivation", values: group.values.map((result) => ({ label: dateLabel(result), value: result.normalized_value })) }]} />)}
    {results.length === 0 ? null : <section aria-labelledby="lab-records-heading"><h2 id="lab-records-heading">Source facts and derived values</h2><p>The source-report columns are authoritative for what was recorded. Derived columns are reproducible conveniences and never overwrite the source.</p><div className="table-scroll" tabIndex={0} role="region" aria-label="Laboratory source facts and derived values"><table><thead><tr><th scope="col">Collected</th><th scope="col">Specimen</th><th scope="col">Source analyte</th><th scope="col">Source result</th><th scope="col">Source range / flag</th><th scope="col">Derived analyte</th><th scope="col">Derived result</th><th scope="col">Provenance</th></tr></thead><tbody>{results.map((result) => <tr key={result.id}><td>{dateLabel(result)}</td><td>{specimenLabel(result)}</td><th scope="row">{result.analyte_name}</th><td>{displayedSourceValue(result)}</td><td>{result.original_reference_range ?? "Not reported"}{result.abnormal_flag === null ? "" : ` · source flag ${result.abnormal_flag}`}</td><td>{result.normalized_analyte_name ?? "Not in curated allow-list"}</td><td>{result.normalized_value === null || result.normalized_unit === null ? "Not derived—original preserved" : `${result.normalized_value} ${result.normalized_unit}`}</td><td>{result.source_type.replaceAll("_", " ")} · {result.confirmation_state.replaceAll("_", " ")}{result.laboratory_name === null ? "" : ` · ${result.laboratory_name}`}</td></tr>)}</tbody></table></div></section>}
  </Page>;
}

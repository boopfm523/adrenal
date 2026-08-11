import { useMemo, useState, type SyntheticEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import {
  confirmLabDocument,
  deleteLabDocument,
  getLabDocument,
  getLabDeletionPreview,
  getLabDocuments,
  getLabExtraction,
  getLabResults,
  uploadLabDocument,
  type LabCandidateConfirmation,
  type LabDocument,
  type LabDocumentConfirmation,
  type LabDeletionPreview,
  type LabExtractionDraft,
  type LabResult,
  type RecordedHistoryFilters,
} from "../api/client";
import { useAuth } from "../auth/context";
import { AccessibleLineChart } from "../components/AccessibleLineChart";
import { Page } from "../components/Page";
import { PaginationControls } from "../components/PaginationControls";
import { formatDecimal, formatMeasurement } from "../format";
import { timezoneAbbreviation } from "../time";

interface TrendGroup {
  key: string;
  name: string;
  specimen: string;
  unit: string;
  values: LabResult[];
}

interface LabHistoryView extends RecordedHistoryFilters { resultPage: number; documentPage: number }
function labPage(params: URLSearchParams, name: string): number { const value = params.get(name) ?? ""; return /^\d+$/.test(value) && Number(value) >= 1 ? Number(value) : 1; }
function labHistoryView(search: string, profileTimezone: string): LabHistoryView { const params = new URLSearchParams(search); return { dateFrom: params.get("local_date_from") ?? "", dateTo: params.get("local_date_to") ?? "", timezone: params.get("timezone") ?? profileTimezone, resultPage: labPage(params, "result_page"), documentPage: labPage(params, "document_page") }; }
function labHistorySearch(view: LabHistoryView): URLSearchParams { const params = new URLSearchParams({ timezone: view.timezone }); if (view.dateFrom !== "") params.set("local_date_from", view.dateFrom); if (view.dateTo !== "") params.set("local_date_to", view.dateTo); if (view.resultPage > 1) params.set("result_page", view.resultPage.toString()); if (view.documentPage > 1) params.set("document_page", view.documentPage.toString()); return params; }

function displayedSourceValue(result: LabResult): string {
  if (result.original_value !== null) return result.original_unit === null ? formatDecimal(result.original_value) : formatMeasurement(result.original_value, result.original_unit);
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
  return `${result.specimen_time.local_time.replace("T", " ")} ${timezoneAbbreviation(result.specimen_time.timezone, result.specimen_time.occurred_at)}`;
}

function optional(value: FormDataEntryValue | null): string | null {
  const normalized = typeof value === "string" ? value.trim() : "";
  return normalized === "" ? null : normalized;
}

function sourcePreviewUrl(documentId: string, pageNumber: number): string {
  return `/api/v1/labs/documents/${documentId}/pages/${String(pageNumber)}/preview`;
}

function sourceDownloadUrl(documentId: string): string {
  return `/api/v1/labs/documents/${documentId}/download`;
}

function UploadPanel({ onUploaded }: { onUploaded: (document: LabDocument) => void }): React.JSX.Element {
  const queryClient = useQueryClient();
  const upload = useMutation({
    mutationFn: uploadLabDocument,
    onSuccess: async (document) => {
      onUploaded(document);
      await queryClient.invalidateQueries({ queryKey: ["lab-documents"] });
    },
  });
  function submit(event: SyntheticEvent<HTMLFormElement, SubmitEvent>): void {
    event.preventDefault();
    const file = new FormData(event.currentTarget).get("file");
    if (file instanceof File && file.size > 0) upload.mutate(file);
  }
  return <section aria-labelledby="pdf-import-heading">
    <h2 id="pdf-import-heading">Import and review a lab PDF</h2>
    <p>Upload creates a private extraction draft only. Nothing enters charts, reports, or your health record until you review and confirm it.</p>
    <form className="lab-upload-form" onSubmit={submit} aria-label="Upload lab PDF">
      <label>PDF file<input required name="file" type="file" accept="application/pdf,.pdf" /></label>
      <button type="submit" disabled={upload.isPending}>{upload.isPending ? "Uploading privately…" : "Upload for review"}</button>
    </form>
    {upload.isError ? <p className="error-summary" role="alert">The PDF could not be uploaded. Use a non-interactive PDF up to 25 MB.</p> : null}
  </section>;
}

function IdList({ label, ids }: { label: string; ids: string[] }): React.JSX.Element {
  return <div><dt>{label}</dt><dd>{ids.length === 0 ? "None" : ids.join(", ")}</dd></div>;
}

function DeletionImpact({ preview }: { preview: LabDeletionPreview }): React.JSX.Element {
  return <>
    <p><strong>{preview.mode === "confirmed_report" ? "Confirmed recorded facts will be permanently deleted." : "This upload has not become a recorded fact."}</strong></p>
    <div className="table-scroll" tabIndex={0} role="region" aria-label="Lab report deletion impact">
      <table><thead><tr><th scope="col">Affected unit</th><th scope="col">Count</th></tr></thead><tbody>
        <tr><th scope="row">Source document</th><td>1</td></tr>
        <tr><th scope="row">Extraction drafts</th><td>{formatDecimal(preview.extraction_draft_ids.length)}</td></tr>
        <tr><th scope="row">Recorded lab panels</th><td>{formatDecimal(preview.panel_ids.length)}</td></tr>
        <tr><th scope="row">Recorded lab results</th><td>{formatDecimal(preview.result_ids.length)}</td></tr>
        <tr><th scope="row">Derived normalized results</th><td>{formatDecimal(preview.derived_result_count)}</td></tr>
        <tr><th scope="row">Trend points</th><td>{formatDecimal(preview.trend_point_count)}</td></tr>
        <tr><th scope="row">AI analyses containing these sources</th><td>{formatDecimal(preview.ai_analysis_ids.length)}</td></tr>
        <tr><th scope="row">Immutable physician-report snapshots</th><td>{formatDecimal(preview.report_snapshot_ids.length)}</td></tr>
        <tr><th scope="row">Rendered report artifacts</th><td>{formatDecimal(preview.report_artifact_ids.length)}</td></tr>
        <tr><th scope="row">Inert page previews</th><td>{formatDecimal(preview.page_preview_count)}</td></tr>
        <tr><th scope="row">Private document-storage artifacts</th><td>{formatDecimal(preview.private_storage_artifact_count)}</td></tr>
      </tbody></table>
    </div>
    <details><summary>Show exact affected record IDs</summary><dl className="data-list">
      <IdList label="Document ID" ids={[preview.document_id]} />
      <IdList label="Draft IDs" ids={preview.extraction_draft_ids} />
      <IdList label="Panel IDs" ids={preview.panel_ids} />
      <IdList label="Result IDs" ids={preview.result_ids} />
      <IdList label="AI analysis IDs" ids={preview.ai_analysis_ids} />
      <IdList label="Report snapshot IDs" ids={preview.report_snapshot_ids} />
      <IdList label="Report artifact IDs" ids={preview.report_artifact_ids} />
    </dl></details>
    {preview.report_snapshot_ids.length === 0 ? null : <p className="draft-warning">Because saved physician reports are immutable copies, every listed snapshot and all of its rendered files will be deleted in full, including unrelated content in that same snapshot.</p>}
    <p>Live trends, reports, exports, and data-quality views update from the remaining records. Encrypted backups may retain deleted copies until their configured expiry.</p>
  </>;
}

function DocumentDeletion({ document, onDeleted }: { document: LabDocument; onDeleted: () => void }): React.JSX.Element {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const preview = useQuery({ queryKey: ["lab-deletion-preview", document.document_id], queryFn: () => getLabDeletionPreview(document.document_id), enabled: open });
  const deletion = useMutation({
    mutationFn: ({ password, confirmation }: { password: string | null; confirmation: string }) => deleteLabDocument(document.document_id, { password, confirmation }),
    onSuccess: async () => {
      onDeleted();
      queryClient.removeQueries({ queryKey: ["lab-document", document.document_id] });
      queryClient.removeQueries({ queryKey: ["lab-extraction", document.document_id] });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["lab-documents"] }),
        queryClient.invalidateQueries({ queryKey: ["lab-results"] }),
        queryClient.invalidateQueries({ queryKey: ["reports"] }),
        queryClient.invalidateQueries({ queryKey: ["data-quality"] }),
      ]);
    },
  });
  return <details className="danger-zone" onToggle={(event) => { setOpen(event.currentTarget.open); }}>
    <summary>Review permanent deletion</summary>
    {preview.isPending ? <p role="status">Calculating exact dependencies…</p> : null}
    {preview.isError ? <p className="error-summary" role="alert">Deletion impact could not be loaded. Nothing was deleted.</p> : null}
    {preview.data === undefined ? null : <>
      <DeletionImpact preview={preview.data} />
      <form className="privacy-action" aria-label={`Delete lab report ${document.display_name}`} onSubmit={(event) => {
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        deletion.mutate({ password: preview.data.requires_password ? data.get("password") as string : null, confirmation: data.get("confirmation") as string });
      }}>
        {preview.data.requires_password ? <label>Current password<input name="password" type="password" required autoComplete="current-password" /></label> : null}
        <label>Type {preview.data.confirmation_phrase}<input name="confirmation" required autoComplete="off" pattern={preview.data.confirmation_phrase} /></label>
        <button type="submit" disabled={deletion.isPending}>{deletion.isPending ? "Queueing permanent deletion…" : preview.data.requires_password ? "Permanently delete confirmed report unit" : "Permanently delete unconfirmed upload"}</button>
      </form>
      {deletion.isError ? <p className="error-summary" role="alert">Nothing was deleted. Check the password and exact phrase, then reload the dependency preview before retrying.</p> : null}
    </>}
  </details>;
}

function DocumentList({ documents, selectedId, select, onDeleted }: { documents: LabDocument[]; selectedId: string | null; select: (id: string) => void; onDeleted: (id: string) => void }): React.JSX.Element {
  if (documents.length === 0) return <p>No lab PDFs uploaded yet.</p>;
  return <section aria-labelledby="lab-documents-heading">
    <h2 id="lab-documents-heading">Uploaded lab documents</h2>
    <div className="table-scroll" tabIndex={0} role="region" aria-label="Uploaded laboratory document history table"><table><caption>Private laboratory documents ordered by upload time, latest first.</caption><thead><tr><th scope="col">Uploaded</th><th scope="col">Document</th><th scope="col">Validation and recording state</th><th scope="col">Review and source actions</th><th scope="col">Deletion</th></tr></thead><tbody>{documents.map((document) => {
      const resolved = document.draft_state === "confirmed" || document.draft_state === "edited";
      return <tr key={document.document_id}>
        <td><time dateTime={document.created_at}>{new Date(document.created_at).toLocaleString()}</time></td><th scope="row">{document.display_name}<span>{document.page_count === null ? "Validation pending" : `${String(document.page_count)} page${document.page_count === 1 ? "" : "s"}`}</span></th>
        <td>{resolved ? "Confirmed into recorded facts" : document.status === "rejected" ? `Rejected: ${document.rejection_reason ?? "validation failed"}` : "Not recorded—review required"}</td>
        <td><div className="quick-actions">
          {resolved ? <a className="button-link" href={sourcePreviewUrl(document.document_id, 1)} target="_blank" rel="noreferrer">View first source page</a> : <button type="button" className={selectedId === document.document_id ? undefined : "button-secondary"} disabled={document.status === "rejected"} onClick={() => { select(document.document_id); }}>{selectedId === document.document_id ? "Review open below" : "Open review"}</button>}
          <a href={sourceDownloadUrl(document.document_id)}>Download original PDF</a>
        </div></td>
        <td><DocumentDeletion document={document} onDeleted={() => { onDeleted(document.document_id); }} /></td>
      </tr>;
    })}</tbody></table></div>
  </section>;
}

interface ReviewCandidate extends LabCandidateConfirmation {
  page_number: number;
  row_index: number;
  extraction_tier: string;
  confidence: number;
  flags: string[];
}

function ReviewForm({ document, draft, timezone, done }: { document: LabDocument; draft: LabExtractionDraft; timezone: string; done: () => void }): React.JSX.Element {
  const queryClient = useQueryClient();
  const initial = draft.candidates.flatMap((candidate, candidateIndex): ReviewCandidate[] => candidate.parsed && candidate.analyte_name !== null && candidate.original_value !== null ? [{
    candidate_index: candidateIndex,
    included: true,
    analyte_name: candidate.analyte_name,
    original_value: candidate.original_value,
    original_unit: candidate.original_unit,
    original_reference_range: candidate.original_reference_range,
    page_number: candidate.page_number,
    row_index: candidate.row_index,
    extraction_tier: candidate.extraction_tier,
    confidence: candidate.confidence,
    flags: candidate.flags,
  }] : []);
  const [candidates, setCandidates] = useState(initial);
  const [activePage, setActivePage] = useState(initial[0]?.page_number ?? 1);
  const [previewFailed, setPreviewFailed] = useState(false);
  const confirmation = useMutation({
    mutationFn: (payload: LabDocumentConfirmation) => confirmLabDocument(document.document_id, payload),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["lab-results"] }),
        queryClient.invalidateQueries({ queryKey: ["lab-documents"] }),
      ]);
      done();
    },
  });
  const unparsed = draft.candidates.filter((candidate) => !candidate.parsed);
  function update(index: number, changes: Partial<ReviewCandidate>): void {
    setCandidates(candidates.map((candidate, position) => position === index ? { ...candidate, ...changes } : candidate));
  }
  function submit(event: SyntheticEvent<HTMLFormElement, SubmitEvent>): void {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    confirmation.mutate({
      specimen_time: { local_time: data.get("specimen_time") as string, timezone: data.get("timezone") as string },
      report_time: { local_time: data.get("report_time") as string, timezone: data.get("timezone") as string },
      laboratory_name: optional(data.get("laboratory_name")),
      accession_id: optional(data.get("accession_id")),
      specimen_type: optional(data.get("specimen_type")),
      report_status: optional(data.get("report_status")),
      candidates: candidates.map(({ candidate_index, included, analyte_name, original_value, original_unit, original_reference_range }) => ({ candidate_index, included, analyte_name, original_value, original_unit, original_reference_range })),
    });
  }
  return <section className="lab-review" aria-labelledby="lab-review-heading">
    <h2 id="lab-review-heading">Review extraction: {document.display_name}</h2>
    <p className="draft-warning">Extraction draft—not recorded. Compare every included value with the source page. HealthCurve does not interpret or recommend treatment.</p>
    <div className="lab-review-layout">
      <div className="lab-source-panel">
        <h3>Source document · page {activePage}</h3>
        <img key={activePage} alt={`Inert preview of source lab document ${document.display_name}, page ${String(activePage)}`} src={sourcePreviewUrl(document.document_id, activePage)} onError={() => { setPreviewFailed(true); }} />
        {previewFailed ? <p className="error-summary" role="alert">This inert page preview is unavailable, so confirmation is disabled. The local document worker will retry it.</p> : null}
        <p><a href={sourcePreviewUrl(document.document_id, activePage)} target="_blank" rel="noreferrer">Open inert source-page preview in a new tab</a> · <a href={sourceDownloadUrl(document.document_id)}>Download original PDF</a></p>
      </div>
      <form className="lab-review-form" onSubmit={submit} aria-label="Confirm extracted lab results">
        <fieldset className="lab-panel-metadata"><legend>Specimen and report context</legend>
          <label>Specimen collection local time<input required name="specimen_time" type="datetime-local" /></label>
          <label>Report local time<input required name="report_time" type="datetime-local" /></label>
          <label>IANA timezone<input required name="timezone" defaultValue={timezone} /></label>
          <label>Specimen type<input name="specimen_type" placeholder="For example: Serum" /></label>
          <label>Laboratory<input name="laboratory_name" /></label>
          <label>Accession ID<input name="accession_id" /></label>
          <label>Report status<input name="report_status" /></label>
        </fieldset>
        <h3>Candidate results</h3>
        {candidates.length === 0 ? <p className="error-summary" role="alert">No parsed rows can be confirmed. The unparsed evidence remains visible below.</p> : candidates.map((candidate, index) => <fieldset className="lab-candidate" key={`${String(candidate.page_number)}-${String(candidate.row_index)}`}><legend>Page {candidate.page_number}, row {candidate.row_index}</legend>
          <p>{candidate.extraction_tier.replaceAll("_", " ")} extraction · confidence {Math.round(candidate.confidence * 100)}%{candidate.flags.length === 0 ? "" : ` · ${candidate.flags.join(", ").replaceAll("_", " ")}`}</p>
          <label className="checkbox-label"><input type="checkbox" checked={candidate.included} onChange={(event) => { update(index, { included: event.target.checked }); }} />Include this row when I confirm</label>
          <button type="button" className="button-secondary" onClick={() => { setPreviewFailed(false); setActivePage(candidate.page_number); }}>Show source page {candidate.page_number}</button>
          <label>Analyte<input required={candidate.included} disabled={!candidate.included} value={candidate.analyte_name} onChange={(event) => { update(index, { analyte_name: event.target.value }); }} /></label>
          <label>Value<input required={candidate.included} disabled={!candidate.included} value={candidate.original_value} onChange={(event) => { update(index, { original_value: event.target.value }); }} /></label>
          <label>Unit<input disabled={!candidate.included} value={candidate.original_unit ?? ""} onChange={(event) => { update(index, { original_unit: event.target.value === "" ? null : event.target.value }); }} /></label>
          <label>Reference range<input disabled={!candidate.included} value={candidate.original_reference_range ?? ""} onChange={(event) => { update(index, { original_reference_range: event.target.value === "" ? null : event.target.value }); }} /></label>
        </fieldset>)}
        <button type="submit" disabled={previewFailed || confirmation.isPending || candidates.length === 0 || !candidates.some((candidate) => candidate.included)}>{confirmation.isPending ? "Recording reviewed facts…" : "Confirm included rows as recorded facts"}</button>
        {confirmation.isError ? <p className="error-summary" role="alert">Nothing was recorded. Check the times and every included row, then try again.</p> : null}
      </form>
    </div>
    {unparsed.length === 0 ? null : <details className="lab-unparsed"><summary>Unparsed evidence requiring manual entry ({unparsed.length})</summary><p>These lines were not guessed and cannot be confirmed from this draft.</p><ul>{unparsed.map((candidate) => <li key={`${String(candidate.page_number)}-${String(candidate.row_index)}`}><strong>Page {candidate.page_number}:</strong> {candidate.source_text || "No readable text"} · {candidate.flags.join(", ").replaceAll("_", " ")}</li>)}</ul></details>}
  </section>;
}

function DocumentReview({ documentId, timezone, close }: { documentId: string; timezone: string; close: () => void }): React.JSX.Element {
  const status = useQuery({
    queryKey: ["lab-document", documentId],
    queryFn: () => getLabDocument(documentId),
    refetchInterval: (query) => query.state.data?.extraction_status === "draft_ready" || query.state.data?.status === "rejected" ? false : 1500,
  });
  const extraction = useQuery({
    queryKey: ["lab-extraction", documentId],
    queryFn: () => getLabExtraction(documentId),
    enabled: status.data?.extraction_status === "draft_ready",
  });
  if (status.isError) return <p className="error-summary" role="alert">The document status could not be loaded.</p>;
  if (status.data?.status === "rejected") return <p className="error-summary" role="alert">The PDF was rejected during private structural validation: {status.data.rejection_reason ?? "validation failed"}.</p>;
  if (status.data?.extraction_status !== "draft_ready" || extraction.data === undefined) return <p role="status">Validating and extracting locally… This page updates automatically.</p>;
  if (extraction.isError) return <p className="error-summary" role="alert">The extraction draft could not be loaded.</p>;
  return <ReviewForm key={extraction.data.draft_id} document={status.data} draft={extraction.data} timezone={timezone} done={close} />;
}

export function LabsPage(): React.JSX.Element {
  const timezone = useAuth().session?.user.defaultTimezone ?? "UTC";
  const [searchParams, setSearchParams] = useSearchParams();
  const appliedSearch = searchParams.toString();
  const view = useMemo(() => labHistoryView(appliedSearch, timezone), [appliedSearch, timezone]);
  const filters = useMemo<RecordedHistoryFilters>(() => ({ dateFrom: view.dateFrom, dateTo: view.dateTo, timezone: view.timezone }), [view.dateFrom, view.dateTo, view.timezone]);
  const [draftState, setDraftState] = useState({ search: appliedSearch, filters });
  const draft = draftState.search === appliedSearch ? draftState.filters : filters;
  const setDraft = (next: RecordedHistoryFilters): void => { setDraftState({ search: appliedSearch, filters: next }); };
  const invalidRange = filters.dateFrom !== "" && filters.dateTo !== "" && filters.dateFrom > filters.dateTo;
  const [validation, setValidation] = useState<string | null>(null);
  const resultsQuery = useQuery({ queryKey: ["lab-results", filters, view.resultPage], queryFn: () => getLabResults(filters, view.resultPage), enabled: !invalidRange });
  const documentsQuery = useQuery({ queryKey: ["lab-documents", filters, view.documentPage], queryFn: () => getLabDocuments(filters, view.documentPage), enabled: !invalidRange });
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(null);
  const results = resultsQuery.data?.items ?? [];
  const groups = trendGroups(results);
  return <Page title="Laboratory results" description="Review private PDF extraction drafts, then view recorded source facts and deterministic trends. HealthCurve does not diagnose, interpret cortisol, or recommend treatment.">
    <aside className="safety-note"><strong>Descriptive records only.</strong> Reference ranges are preserved exactly from each source and are never invented or used here to diagnose. Cortisol collection time and specimen type materially affect context; discuss interpretation with your physician.</aside>
    <UploadPanel onUploaded={(document) => { setSelectedDocumentId(document.document_id); }} />
    <form className="filter-panel" onSubmit={(event) => { event.preventDefault(); if (draft.dateFrom !== "" && draft.dateTo !== "" && draft.dateFrom > draft.dateTo) { setValidation("From date must be on or before Through date."); return; } setValidation(null); setSelectedDocumentId(null); setSearchParams(labHistorySearch({ ...draft, resultPage: 1, documentPage: 1 })); }}><label>From date<input type="date" value={draft.dateFrom} onChange={(event) => { setDraft({ ...draft, dateFrom: event.target.value }); }} /></label><label>Through date<input type="date" value={draft.dateTo} onChange={(event) => { setDraft({ ...draft, dateTo: event.target.value }); }} /></label><label>History IANA timezone<input required value={draft.timezone} onChange={(event) => { setDraft({ ...draft, timezone: event.target.value }); }} /></label>{validation === null && !invalidRange ? null : <p className="error-summary form-wide" role="alert">{validation ?? "From date must be on or before Through date."}</p>}<div className="filter-actions"><button type="submit">Apply history filters</button><button className="button-secondary" type="button" onClick={() => { const reset = { dateFrom: "", dateTo: "", timezone }; setValidation(null); setSelectedDocumentId(null); setDraftState({ search: "", filters: reset }); setSearchParams(new URLSearchParams()); }}>Clear history filters</button></div></form>
    <p className="privacy-note">Inclusive dates use {timezoneAbbreviation(filters.timezone)}. Result dates mean specimen collection; document dates mean private upload time.</p>
    {documentsQuery.isFetching ? <p role="status">Loading lab documents…</p> : null}
    {documentsQuery.isError ? <p className="error-summary" role="alert">Lab documents could not be loaded.</p> : null}
    {documentsQuery.data === undefined ? null : <><DocumentList documents={documentsQuery.data.items} selectedId={selectedDocumentId} select={setSelectedDocumentId} onDeleted={(id) => { if (selectedDocumentId === id) setSelectedDocumentId(null); if (view.documentPage > 1 && documentsQuery.data.items.length === 1) setSearchParams(labHistorySearch({ ...view, documentPage: view.documentPage - 1 })); }} /><PaginationControls label="Lab document history" metadata={documentsQuery.data.page} onPageChange={(documentPage) => { setSelectedDocumentId(null); setSearchParams(labHistorySearch({ ...view, documentPage })); }} /></>}
    {selectedDocumentId === null ? null : <DocumentReview documentId={selectedDocumentId} timezone={timezone} close={() => { setSelectedDocumentId(null); }} />}
    <hr />
    {resultsQuery.isFetching ? <p role="status">Loading laboratory facts…</p> : null}
    {resultsQuery.isError ? <p role="alert" className="error-summary">Laboratory facts could not be loaded.</p> : null}
    {!resultsQuery.isFetching && !resultsQuery.isError && results.length === 0 ? <p>No laboratory facts recorded.</p> : null}
    {groups.map((group) => <AccessibleLineChart key={group.key} title={`${group.name} — ${group.specimen}`} summary="Each point is one recorded specimen on the visible results page. Lines are descriptive only; missing intervals are not inferred." unit={group.unit} timezone={timezone} timezoneReferenceDate={group.values.at(-1)?.specimen_time.local_time.slice(0, 10)} dateRange={`${group.values[0]?.specimen_time.local_time.slice(0, 10) ?? "Unavailable"} through ${group.values.at(-1)?.specimen_time.local_time.slice(0, 10) ?? "Unavailable"}`} definition={`Values on this results page use ${group.values[0]?.normalization_method ?? "the recorded deterministic normalization rule"}. Results are grouped only when canonical analyte, specimen type, and normalized unit match.`} sampleCount={group.values.length} missingCount={0} series={[{ name: group.name, source: "recorded lab facts with deterministic derivation", values: group.values.map((result) => ({ label: dateLabel(result), value: result.normalized_value })) }]} />)}
    {results.length === 0 ? null : <section aria-labelledby="lab-records-heading"><h2 id="lab-records-heading">Source facts and derived values</h2><p>The source-report columns are authoritative for what was recorded. Derived columns are reproducible conveniences and never overwrite the source.</p><div className="table-scroll" tabIndex={0} role="region" aria-label="Laboratory source facts and derived values"><table><thead><tr><th scope="col">Collected</th><th scope="col">Specimen</th><th scope="col">Source analyte</th><th scope="col">Source result</th><th scope="col">Source range / flag</th><th scope="col">Derived analyte</th><th scope="col">Derived result</th><th scope="col">Provenance</th></tr></thead><tbody>{results.map((result) => <tr key={result.id}><td>{dateLabel(result)}</td><td>{specimenLabel(result)}</td><th scope="row">{result.analyte_name}</th><td>{displayedSourceValue(result)}</td><td>{result.original_reference_range ?? "Not reported"}{result.abnormal_flag === null ? "" : ` · source flag ${result.abnormal_flag}`}</td><td>{result.normalized_analyte_name ?? "Not in curated allow-list"}</td><td>{result.normalized_value === null || result.normalized_unit === null ? "Not derived—original preserved" : formatMeasurement(result.normalized_value, result.normalized_unit)}</td><td>{result.source_type.replaceAll("_", " ")} · {result.confirmation_state.replaceAll("_", " ")}{result.laboratory_name === null ? "" : ` · ${result.laboratory_name}`}{result.source_document_id === null ? "" : <><br />{result.source_page_number === null ? <a href={sourceDownloadUrl(result.source_document_id)}>Download original PDF</a> : <><a href={sourcePreviewUrl(result.source_document_id, result.source_page_number)} target="_blank" rel="noreferrer">View source page {String(result.source_page_number)}</a> · <a href={sourceDownloadUrl(result.source_document_id)}>Download original PDF</a></>}</>}</td></tr>)}</tbody></table></div>{resultsQuery.data === undefined ? null : <PaginationControls label="Laboratory result records" metadata={resultsQuery.data.page} onPageChange={(resultPage) => { setSearchParams(labHistorySearch({ ...view, resultPage })); }} />}</section>}
  </Page>;
}

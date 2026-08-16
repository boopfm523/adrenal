import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  AnalysisRequestCancelledError,
  AnalysisRequestTimeoutError,
  PATTERN_ANALYSIS_REQUEST_TIMEOUT_SECONDS,
  deletePatternAnalysis,
  generatePatternAnalysis,
  getPatternAnalysis,
  type PatternAnalysis,
} from "../api/client";

export function PatternAnalysisCard({ dateFrom, dateTo, timezone }: { dateFrom: string; dateTo: string; timezone: string }): React.JSX.Element {
  const queryClient = useQueryClient();
  const queryKey = ["pattern-analysis", dateFrom, dateTo, timezone];
  const controllerRef = useRef<AbortController | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [attempted, setAttempted] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const saved = useQuery({ queryKey, queryFn: () => getPatternAnalysis(dateFrom, dateTo, timezone) });
  const generate = useMutation({
    mutationFn: (signal: AbortSignal) => generatePatternAnalysis(dateFrom, dateTo, timezone, signal),
    onSuccess: (result) => {
      setNotice(result.detail ?? null);
      if (result.analysis !== null) queryClient.setQueryData<PatternAnalysis | null>(queryKey, result.analysis);
    },
    onSettled: () => { controllerRef.current = null; },
  });
  const remove = useMutation({
    mutationFn: (analysisId: string) => deletePatternAnalysis(analysisId),
    onSuccess: () => {
      queryClient.setQueryData<PatternAnalysis | null>(queryKey, null);
      setNotice("The generated draft was deleted. Recorded facts, plans, and deterministic analytics were unchanged.");
    },
  });
  const analysis = generate.data?.analysis ?? saved.data ?? null;

  useEffect(() => {
    if (!generate.isPending) return;
    const startedAt = Date.now();
    const interval = window.setInterval(() => {
      setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);
    return () => { window.clearInterval(interval); };
  }, [generate.isPending]);

  useEffect(() => () => { controllerRef.current?.abort(); }, []);

  function start(): void {
    if (generate.isPending || controllerRef.current !== null) return;
    generate.reset();
    setElapsedSeconds(0);
    setAttempted(true);
    setNotice(null);
    const controller = new AbortController();
    controllerRef.current = controller;
    generate.mutate(controller.signal);
  }

  const cancelled = generate.error instanceof AnalysisRequestCancelledError;
  const timedOut = generate.error instanceof AnalysisRequestTimeoutError;
  const generateLabel = generate.isPending
    ? "Waiting for private Ollama…"
    : attempted
      ? "Try private analysis again"
      : analysis === null
        ? "Ask Ollama to explain these patterns"
        : "Ask Ollama to refresh this draft";

  return <section className="category-card category-card--ai pattern-analysis-card" aria-labelledby="pattern-analysis-title">
    <h3 id="pattern-analysis-title">Private Ollama pattern explanation</h3>
    <p>This asks the configured Ollama model running on your private host to explain only the deterministic range table above. Ollama does not calculate the HealthCurve, change recorded facts or physician-approved plans, or give dosing advice. No health text is sent to a cloud AI service.</p>
    <p>Only a completed draft that passes HealthCurve’s citation and medication-safety checks is saved. A refresh reloads the latest saved draft for this exact date range and timezone; failed or unfinished requests are not saved.</p>
    <div className="analysis-actions">
      <button type="button" onClick={start} disabled={generate.isPending || remove.isPending}>{generateLabel}</button>
      {generate.isPending ? <button type="button" className="button-secondary" onClick={() => { controllerRef.current?.abort(); }}>Stop waiting</button> : null}
      {(cancelled || timedOut) ? <button type="button" className="button-secondary" onClick={() => { void saved.refetch(); }}>Check for a completed draft</button> : null}
    </div>
    {generate.isPending ? <p role="status" aria-live="polite">Private host Ollama request in progress: {elapsedSeconds.toString()} seconds elapsed. This browser will stop waiting after {PATTERN_ANALYSIS_REQUEST_TIMEOUT_SECONDS.toString()} seconds.</p> : null}
    {saved.isPending && analysis === null ? <p role="status">Checking for a saved private-model draft…</p> : null}
    {saved.isError ? <p className="error-summary" role="alert">The saved draft could not be checked. The deterministic range table remains available, and you can still try a new private analysis.</p> : null}
    {cancelled ? <p role="status">This browser stopped waiting. Ollama may still finish locally; use “Check for a completed draft” in a moment or refresh this page. Recorded facts, plans, and deterministic analytics are unchanged.</p> : null}
    {timedOut ? <p className="error-summary" role="alert">The browser stopped waiting after {PATTERN_ANALYSIS_REQUEST_TIMEOUT_SECONDS.toString()} seconds. Ollama may still be loading or may finish locally. Check for a completed draft, or try again; deterministic analytics remain available.</p> : null}
    {generate.isError && !cancelled && !timedOut ? <p className="error-summary" role="alert">The private host request could not be completed. Confirm that Ollama is running and the configured model is installed, then try again. Deterministic analytics remain available.</p> : null}
    {notice === null ? null : <p role="status">{notice}</p>}
    {analysis === null ? <p>No completed private-model draft is saved for this date range and timezone.</p> : <div>
      <aside className="draft-warning">Generated analysis—not medical advice, a diagnosis, a cortisol measurement, or a physician-approved plan.</aside>
      <pre className="report-record">{analysis.body}</pre>
      <details className="metric-definition"><summary>AI draft provenance</summary><p><strong>Sources:</strong> {analysis.source_record_ids.length.toString()} daily feature IDs. <strong>Model:</strong> {analysis.model_name} ({analysis.model_digest}). <strong>Prompt/schema:</strong> {analysis.prompt_version} / {analysis.schema_version}.</p></details>
      <button type="button" onClick={() => { remove.mutate(analysis.id); }} disabled={generate.isPending || remove.isPending}>{remove.isPending ? "Deleting generated draft…" : "Delete generated draft"}</button>
      {remove.isError ? <p className="error-summary" role="alert">The generated draft was not deleted. Recorded facts and plans were unchanged.</p> : null}
    </div>}
  </section>;
}

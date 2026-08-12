import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { generateDayAnalysis, getDayAnalysis, type DayAnalysis } from "../api/client";

export function DayAnalysisCard({ day, timezone }: { day: string; timezone: string }): React.JSX.Element {
  const queryClient = useQueryClient();
  const queryKey = ["healthcurve-day-analysis", day, timezone];
  const saved = useQuery({
    queryKey,
    queryFn: () => getDayAnalysis(day, timezone),
    refetchInterval: 60_000,
  });
  const generate = useMutation({
    mutationFn: () => generateDayAnalysis(day, timezone),
    onSuccess: (result) => {
      if (result.analysis !== null) queryClient.setQueryData<DayAnalysis | null>(queryKey, result.analysis);
    },
  });
  const result = generate.data;
  const analysis = result?.analysis ?? saved.data ?? null;
  const notice = result?.detail ?? null;

  return <section className="category-card category-card--ai healthcurve-day-analysis" aria-labelledby="healthcurve-day-analysis-title">
    <h2 id="healthcurve-day-analysis-title">AI HealthCurve analysis</h2>
    <p>Ask your private host Ollama model to review all available data for this day, including sensitive diary and life-event text when present. Dense wearable readings are summarized into fixed time windows; nothing is sent to a cloud AI service.</p>
    <aside className="draft-warning"><strong>Exploratory AI interpretation.</strong> It may highlight temporal associations or questions, but cannot diagnose, establish causation, measure cortisol, determine medication need, advise dosing, or change recorded facts and physician-approved plans.</aside>
    <button type="button" disabled={generate.isPending} onClick={() => { generate.mutate(); }}>{generate.isPending ? "Analyzing this day…" : analysis === null ? "Analyze this day" : "Analyze this day again"}</button>
    {saved.isPending && analysis === null ? <p role="status">Checking for a saved analysis…</p> : null}
    {saved.isError ? <p className="error-summary" role="alert">A saved analysis could not be checked. You can still try a new analysis.</p> : null}
    {generate.isError ? <p className="error-summary" role="alert">The private-model request could not be completed. The HealthCurve and recorded data are unchanged.</p> : null}
    {notice === null ? null : <p role="status">{notice}</p>}
    {analysis === null ? <p>No AI interpretation has been saved for this selected day and timezone.</p> : <div className="healthcurve-day-analysis-result">
      {analysis.stale ? <p className="error-summary" role="alert"><strong>Recorded data changed after this analysis.</strong> Analyze this day again to include the latest facts.</p> : <p className="success-summary"><strong>Current for this source revision.</strong></p>}
      <pre className="report-record">{analysis.body}</pre>
      <details className="metric-definition"><summary>AI analysis provenance</summary><dl className="metric-metadata"><div><dt>Selected day</dt><dd>{analysis.selected_date}</dd></div><div><dt>Timezone</dt><dd>{analysis.timezone}</dd></div><div><dt>Model</dt><dd>{analysis.model_name}</dd></div><div><dt>Model digest</dt><dd><code>{analysis.model_digest}</code></dd></div><div><dt>Prompt/schema</dt><dd>{analysis.prompt_version} / {analysis.schema_version}</dd></div><div><dt>Source-day revision</dt><dd><code>{analysis.source_revision_sha256}</code></dd></div></dl></details>
    </div>}
  </section>;
}

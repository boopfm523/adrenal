import { useState } from "react";
import { Link } from "react-router-dom";

import helpContent from "../helpContent.json";
import { AiAnalysisCard, FactCard, PlanCard } from "../components/CategoryCards";
import { Page } from "../components/Page";

interface Endpoint {
  method: string;
  path: string;
}

interface Workflow {
  id: string;
  title: string;
  availability: string;
  webPath?: string;
  instructions: string;
  example: string;
  result: string;
  confirmation: string;
  endpoints: Endpoint[];
}

function CopyableExample({ label, value }: { label: string; value: string }): React.JSX.Element {
  const [message, setMessage] = useState("");

  async function copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(value);
      setMessage("Copied.");
    } catch {
      setMessage("Copy failed. Select the example text instead.");
    }
  }

  return (
    <div className="copy-example">
      <pre tabIndex={0} aria-label={`${label} example`}><code>{value}</code></pre>
      <button className="button-secondary" type="button" onClick={() => { void copy(); }} aria-label={`Copy ${label} example`}>Copy</button>
      <span className="copy-status" role="status" aria-live="polite">{message}</span>
    </div>
  );
}

function EndpointList({ endpoints }: { endpoints: Endpoint[] }): React.JSX.Element {
  return (
    <details className="help-endpoints">
      <summary>Implemented route{endpoints.length === 1 ? "" : "s"}</summary>
      <ul>{endpoints.map((endpoint) => <li key={`${endpoint.method}-${endpoint.path}`}><code>{endpoint.method} {endpoint.path}</code></li>)}</ul>
    </details>
  );
}

function WorkflowCard({ workflow }: { workflow: Workflow }): React.JSX.Element {
  return (
    <article className="help-workflow" id={workflow.id}>
      <div className="help-workflow__heading">
        <h3>{workflow.title}</h3>
        <span className="availability-label">{workflow.availability}</span>
      </div>
      <p>{workflow.instructions}</p>
      <dl className="help-outcome">
        <div><dt>Creates</dt><dd>{workflow.result}</dd></div>
        <div><dt>Review or confirmation</dt><dd>{workflow.confirmation}</dd></div>
      </dl>
      <CopyableExample label={workflow.title} value={workflow.example} />
      {workflow.webPath === undefined ? null : workflow.webPath === "/emergency" ? <a href={workflow.webPath}>Open emergency page</a> : <Link to={workflow.webPath}>Open {workflow.title.toLowerCase()}</Link>}
      <EndpointList endpoints={workflow.endpoints} />
    </article>
  );
}

export function HelpPage(): React.JSX.Element {
  const apiWorkflows: Workflow[] = helpContent.apiWorkflows;
  const importWorkflows: Workflow[] = helpContent.importWorkflows;

  return (
    <Page title="Help" description="Practical, current instructions for recording, reviewing, importing, correcting, and exporting HealthCurve data.">
      <aside className="help-emergency" aria-labelledby="help-emergency-title">
        <h2 id="help-emergency-title">HealthCurve is not emergency care or dosing advice</h2>
        <p>If you may be having an emergency, contact local emergency services and follow your dated physician-authored instructions. HealthCurve records what happened; it does not decide whether, when, or how much medication you should take.</p>
        <a className="button-link button-link--urgent" href="/emergency">Open emergency plan</a>
      </aside>

      <nav className="help-topics" aria-label="Help topics">
        <a href="#record-types">Understand the record</a>
        <a href="#telegram">Telegram</a>
        <a href="#web-entry">Web and API entry</a>
        <a href="#imports">Imports</a>
        <a href="#plan-and-review">Plan, review, and privacy</a>
        <a href="#planned">Not available yet</a>
      </nav>

      <section id="record-types" className="help-section anchor-target" aria-labelledby="record-types-title">
        <h2 id="record-types-title">Know what kind of information you are looking at</h2>
        <div className="help-category-grid">
          <FactCard title="What you reported or imported"><p>Actual doses, symptoms, episodes, injections, labs, wearable measurements, diary entries, and corrections. Source and experienced time stay attached.</p></FactCard>
          <PlanCard title="What your clinician approved"><p>Versioned regimens and physician-authored instructions with approval provenance. Recording an actual dose never changes this plan.</p></PlanCard>
          <AiAnalysisCard title="What software proposed or summarized"><p>Extraction drafts and optional observations remain derived, labeled, and separate. A model cannot approve a plan or directly write a medical fact.</p></AiAnalysisCard>
        </div>
      </section>

      <section id="telegram" className="help-section anchor-target" aria-labelledby="telegram-title">
        <h2 id="telegram-title">Add data with Telegram</h2>
        <p>Free text is sent only to the configured private Ollama model. It produces a draft—not a fact. Review every medication, amount, unit, route, symptom, and experienced time before pressing Confirm.</p>
        <CopyableExample label="Telegram natural language" value="At 07:08 I took 10 mg hydrocortisone and noticed mild nausea." />
        <p><strong>Result:</strong> confirmation-required AI extraction draft. Nothing becomes a recorded fact until you confirm an actionable draft.</p>
        <div className="help-command-grid">
          {helpContent.telegramCommands.map((item) => (
            <article className="help-command" key={item.command}>
              <h3><code>{item.command}</code></h3>
              <p>{item.purpose}</p>
              <p><strong>Syntax:</strong> <code>{item.syntax}</code></p>
              <dl className="help-outcome"><div><dt>Creates</dt><dd>{item.result}</dd></div><div><dt>Review or confirmation</dt><dd>{item.confirmation}</dd></div></dl>
              <CopyableExample label={item.command} value={item.example} />
            </article>
          ))}
        </div>
      </section>

      <section id="web-entry" className="help-section anchor-target" aria-labelledby="web-entry-title">
        <h2 id="web-entry-title">Add or correct data on the web and API</h2>
        <p>Web actions are authenticated. A direct-entry form records a fact when submitted; correction forms create a new version and preserve the original. API writes require the session’s CSRF token as well as the session cookie.</p>
        <div className="help-workflow-grid">{apiWorkflows.map((workflow) => <WorkflowCard key={workflow.id} workflow={workflow} />)}</div>
      </section>

      <section id="imports" className="help-section anchor-target" aria-labelledby="imports-title">
        <h2 id="imports-title">Import files and laboratory data</h2>
        <p>Preview and extraction are review stages. They do not prove that a source is complete, and missing provider values are never filled with zero.</p>
        <div className="help-workflow-grid">{importWorkflows.map((workflow) => <WorkflowCard key={workflow.id} workflow={workflow} />)}</div>
      </section>

      <section id="plan-and-review" className="help-section anchor-target" aria-labelledby="plan-review-title">
        <h2 id="plan-review-title">Set up the plan, review results, and control privacy</h2>
        <div className="help-workflow-grid">
          <article className="help-workflow">
            <h3>Load a medication list and draft regimen</h3>
            <p>Run the local operator command from the project directory. Review the generated YAML against your prescription and physician’s written instructions. Loading creates a <strong>draft plan</strong>, never an approved one.</p>
            <CopyableExample label="Medication template" value="docker compose run --rm api python -m healthcurve.cli init-medications-file medications.yaml" />
            <CopyableExample label="Load medications" value={'docker compose run --rm -v "$PWD/medications.yaml:/tmp/medications.yaml:ro" api python -m healthcurve.cli load-medications /tmp/medications.yaml'} />
            <Link to="/plan">Review plan versions</Link>
          </article>
          <article className="help-workflow">
            <h3>Review timelines, analytics, and data quality</h3>
            <p>The Timeline shows category and provenance. Analytics states definitions, timezone, sample count, and missingness. Data quality is a bounded review queue—not proof that the record is complete.</p>
            <ul><li><Link to="/timeline">Open Timeline</Link></li><li><Link to="/analytics">Open Analytics</Link></li><li><Link to="/data-quality">Open Data quality</Link></li></ul>
          </article>
          <article className="help-workflow">
            <h3>Record blood pressure or weight on the web</h3>
            <p>Health data provides mobile-friendly entry, current facts with source and confirmation provenance, immutable corrections, and trend charts with equivalent data tables. Values are displayed without diagnosis or treatment advice.</p>
            <CopyableExample label="Synthetic web examples" value="Blood pressure: 118/76 mmHg, pulse 62 · Weight: 180 lb" />
            <Link to="/health-data">Open Health data</Link>
          </article>
          <article className="help-workflow">
            <h3>Review laboratory facts and trends</h3>
            <p>Labs accepts a private PDF upload, shows each extraction candidate beside a networkless-rendered inert page preview, and records only rows you explicitly confirm. You can correct or exclude candidates; unconfirmed drafts never enter trends or reports. The original PDF remains attachment-only. Recorded facts keep their exact source page, wording, units, ranges, collection time, specimen type, and provenance beside any versioned derived value. Trend charts include equivalent tables and never combine incompatible specimen types or units. Cortisol is descriptive only.</p>
            <Link to="/labs">Open Labs</Link>
          </article>
          <article className="help-workflow">
            <h3>Create a physician report</h3>
            <p>Select a date range and sections. Facts, approved plan, patient notes, and optional AI remain visibly separated. AI and sensitive notes are off by default.</p>
            <CopyableExample label="Synthetic report range" value="2026-07-01 through 2026-07-31 · America/New_York · AI off" />
            <Link to="/reports">Open Reports</Link>
          </article>
          <article className="help-workflow">
            <h3>Export, disconnect, or delete</h3>
            <p>Settings & privacy supports a password-confirmed private export, integration disconnection with provider-data deletion, independent context deletion, session revocation, MFA, and account deletion. Encrypted backups retain deleted copies until their configured expiry.</p>
            <CopyableExample label="Privacy review" value="Before deleting: export → verify the download → review backup retention → delete the exact record or integration." />
            <Link to="/settings">Open Settings & privacy</Link>
          </article>
        </div>
      </section>

      <section id="planned" className="help-section help-planned anchor-target" aria-labelledby="planned-title">
        <h2 id="planned-title">Clearly not available yet</h2>
        <ul>
          <li><strong>Garmin account connection:</strong> automatic provider sync is not implemented. Only owner-supplied FIT, CSV, or ZIP preview/confirm is available.</li>
          <li><strong>Offsite backup assurance:</strong> encrypted local backup exists, but production readiness still requires an offsite copy and a successful isolated restore drill.</li>
        </ul>
      </section>
    </Page>
  );
}

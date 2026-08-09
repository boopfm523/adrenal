# HealthCurve Agent Instructions

## Project Purpose

HealthCurve is a private personal health-tracking application focused initially on living without adrenal glands and managing adrenal insufficiency. It records medication plans and actual doses, stress/up-dose episodes, emergency injections, symptoms, diary/life events, Garmin data, labs, contextual information, analytics, and physician reports.

HealthCurve is not a diagnostic system, emergency service, or autonomous medication adviser.

## Instruction Precedence

Before implementation, read this file, the HealthCurve project plan, repository documentation and ADRs, `bd prime`, the selected Beads issue and its dependencies, and the relevant code/tests.

If sources conflict, follow this order:

1. Current explicit user instructions.
2. Repository medical-safety, security, and privacy requirements.
3. This `AGENTS.md`.
4. Accepted architecture decision records.
5. The HealthCurve project plan.
6. Individual Beads issues.

A Beads issue never grants permission to violate a higher-priority rule.

## Beads Is Mandatory

This project uses `bd` (Beads) for all durable task tracking and project memory. Run `bd prime` at the beginning of every implementation session and whenever context may be stale.

- Use Beads for every implementation task, bug, discovery, follow-up, dependency, and blocker.
- Do not create Markdown task lists, `TODO.md`, `MEMORY.md`, or another parallel tracking system.
- A narrowly scoped source-code `TODO` is allowed only when it cites a Beads issue ID.
- Use `bd remember` for durable knowledge when supported.
- Inspect local help before assuming unfamiliar syntax, statuses, or relationship names.
- Never invent Beads commands or completion evidence.
- Do not close an epic while required children remain open.
- Create linked issues for discovered work rather than hiding it in commits or silently expanding scope.
- Never place credentials, secrets, real medical data, or sensitive personal information in Beads.

Common commands (verify against the installed version):

```bash
bd prime
bd ready
bd show <id>
bd update <id> --claim
bd close <id>
bd remember
bd dolt status
bd dolt pull
bd dolt push
```

Issues live in the local Beads/Dolt database. Follow the synchronization procedure reported by the installed `bd prime`. `.beads/issues.jsonl` is a passive export, not the normal synchronization protocol. Do not run `bd import` during ordinary work unless documented recovery or migration instructions require it.

## Autonomous Build Authority

This repository opts into the Beads team-maintainer profile.

When the user requests an autonomous HealthCurve build, the agent is authorized to:

- Select, claim, update, and close ready Beads issues.
- Create issues and dependencies for discovered work.
- Implement code, tests, migrations, documentation, and repository configuration.
- Run development services and local verification tools.
- Make ordinary, reversible implementation decisions.
- Commit completed work to the current designated development branch.
- Pull and push ordinary fast-forward changes when the project remote and branch are already configured.
- Synchronize Beads/Dolt state with the configured project remote.
- Continue immediately with the next ready issue without waiting for routine approval.

This authority does not include production deployment, purchases, public infrastructure changes, medical decisions, destructive data operations, or acquiring or repurposing credentials.

## Autonomous Work Loop

Repeat until no eligible ready work remains or a stop condition is reached:

1. Run `bd prime`.
2. Inspect the working tree and branch with `git status --short` and `git branch --show-current`.
3. Preserve existing and unrelated user changes.
4. Run `bd ready`.
5. Choose the highest-priority ready issue whose dependencies are satisfied.
6. Run `bd show <id>` and inspect its parent, children, dependencies, and blockers.
7. Confirm that acceptance criteria are observable. Split or clarify an overly broad issue in Beads.
8. Claim the issue using the locally supported command.
9. Inspect all relevant code, tests, migrations, documentation, and ADRs before editing.
10. Record the approach in Beads when it involves meaningful design judgment.
11. Implement the smallest complete vertical slice satisfying the issue.
12. Add or update relevant tests.
13. Run applicable quality gates and correct failures caused by the work.
14. Complete required security, privacy, clinical-safety, migration, accessibility, and visual checks.
15. Record decisions and verification evidence in Beads.
16. Create linked issues for legitimate deferred work.
17. Close the issue only when every acceptance criterion passes.
18. Review the diff and commit with the Beads ID in the commit message.
19. Synchronize Beads using the installed documented workflow.
20. Push when authorized and safe.
21. Return to `bd ready` and continue without waiting for routine confirmation.

Do not stop after one issue when additional ready work exists.

## Task Selection

Choose work in this order:

1. P0 security, medical-safety, data-integrity, or release-blocking bugs.
2. P0 foundations or work that unblocks multiple issues.
3. P1 bugs affecting existing workflows.
4. Ready work in the earliest incomplete roadmap phase.
5. Lower-priority hardening and enhancements.

Dependencies take precedence over issue-number order. Prefer deterministic foundations before AI-dependent behavior. Never work around a real dependency merely because the blocked issue has high priority.

Treat these concerns as urgent while their corresponding Beads issues remain open:

- Ollama must not listen publicly or on untrusted interfaces without an explicitly approved authenticated design.
- A missing Ollama model must produce a visible, safe fallback.
- Non-deterministic extraction must not silently save incomplete facts.
- Emergency information must not depend on Ollama.
- Recorded facts, physician-approved plans, and AI analysis must stay separated.

## Stop Conditions

Stop and request user input only when:

- A physician-approved medication, stress-dosing, or emergency instruction is required.
- An unresolved choice would materially change medical safety or privacy.
- A required secret, credential, provider account, enrollment, or external authorization is missing.
- A purchase, subscription, legal agreement, billing action, DNS change, public deployment, or external message is required.
- A destructive or difficult-to-reverse Git, database, storage, or infrastructure operation is required.
- Existing user changes conflict with the implementation and cannot safely be preserved.
- A protected approval or permission cannot be obtained.
- Tests repeatedly fail because of an external problem that cannot be resolved locally.
- The same blocker remains after reasonable documented attempts and no independent ready work remains.
- No ready, unblocked work remains or the active goal is complete.

Do not stop for routine choices. Make a safe, conventional, reversible decision, document meaningful reasoning, and continue.

When blocked:

1. Update the issue with the exact blocker and evidence.
2. Record relevant commands and error messages without secrets.
3. Mark or relate the issue appropriately using supported Beads states.
4. Check for other independent ready work.
5. Continue when safe; ask the user only when meaningful independent progress is no longer possible.

## Medical Safety Boundaries

HealthCurve must preserve three separate categories in the database, API, UI, exports, and reports:

1. **Recorded facts:** user-entered or imported events, actual doses, symptoms, labs, wearable measurements, corrections, and provenance.
2. **Physician-approved plans:** versioned regimens and instructions with effective dates, approval state, and provenance.
3. **AI-generated content:** extraction drafts, summaries, observations, explanations, confidence, and model/prompt metadata.

The agent and application must not:

- Invent physician-approved medication or emergency instructions.
- Recommend or autonomously change medication doses.
- Convert AI analysis into an approved plan.
- Present exploratory correlation as diagnosis or causation.
- Alter recorded facts without preserving correction history.
- Treat missing wearable data as zero.
- Make emergency information dependent on an LLM.
- Present HealthCurve as a replacement for emergency or clinical care.

Ambiguous high-impact extracted fields require confirmation before becoming recorded facts, including medication, amount, unit, route, time, stress/up-dose classification, emergency injection details, and lab values/units.

Use synthetic health data in development, tests, fixtures, screenshots, demos, seeds, and LLM evaluations. Never commit real medical records, PDFs, Telegram messages, exact locations, credentials, or patient identifiers.

## Security and Privacy

Treat all health data as highly sensitive.

- Keep PostgreSQL, Redis, Ollama, and administration off the public internet.
- Use least privilege and keep secrets/encryption keys outside Git.
- Encrypt integration tokens at rest and use TLS in transit.
- Enforce ownership and authorization on every user-scoped resource.
- Sanitize user-entered, imported, OCR, and AI-generated content before rendering.
- Treat documents, messages, OCR text, and model input as untrusted.
- Redact logs; do not log raw Telegram bodies, labs, exact locations, medical PDFs, tokens, or diary text by default.
- Preserve correction and plan-version audit history.
- Make export, disconnect, retention, and deletion behavior testable.
- Keep production data out of development and CI.
- Do not send medical information to cloud services unless the user explicitly approves that service and data flow.

A local model does not guarantee a local pipeline. Verify that parsing, OCR, temporary files, telemetry, backups, and supporting services respect the intended privacy boundary.

## Repository and Destructive-Action Safety

Preserve existing user work. Inspect `git status --short` before editing. Do not overwrite, revert, or commit unrelated changes.

Before deleting or overwriting anything:

- Resolve and validate the exact target.
- Confirm it is inside the intended repository scope.
- Ensure it is not a repository/workspace root, home directory, database, backup, unresolved variable, or broad glob.
- Prefer recoverable operations.

Never run `git reset --hard`, destructive `git clean`, force pushes, shared-history rewrites, broad recursive deletion, database drops, or production migration rollback without explicit authorization.

Non-interactive flags may be used for an already authorized exact target; they do not replace target validation.

## Git Workflow

- Keep commits focused and include the Beads ID, for example: `hc-123: implement versioned medication regimen`.
- Do not mix unrelated changes or rewrite user commits.
- Do not bypass hooks or quality gates merely to finish an issue.
- Never commit secrets, private medical data, local environment files, generated caches, or source documents.

Before committing:

1. Review `git status` and the complete diff.
2. Confirm no secrets or unrelated files are staged.
3. Run required quality gates.
4. Record verification evidence in Beads.

Push only when the remote is clearly the HealthCurve remote, the branch is a designated development branch, the update is an ordinary fast-forward, checks pass, and no protected override is needed. Stop for an ambiguous destination, sensitive-data risk, force push, history rewrite, or protected-branch approval.

If Beads synchronization fails, record the exact error, preserve the local Dolt database, and do not impulsively rebuild or re-import it. Stop if additional work could lose or diverge task history.

## Engineering Standards

Prefer a modular monolith until an accepted ADR says otherwise. Maintain boundaries for identity, canonical events, medications/plans, episodes/injections, integrations, labs/documents, AI, analytics, reports, and operations.

Data rules:

- Use decimals and explicit units for medication quantities.
- Store UTC plus original local time, IANA timezone, and UTC offset.
- Preserve source IDs, revisions, confirmation state, and correction provenance.
- Make imports idempotent.
- Keep plan versions historically queryable.
- Do not keep essential invariants only in unvalidated JSON.
- Use and test migrations for schema changes.

AI rules:

- Require schema-constrained output and deterministic validation.
- Preserve model, prompt, schema, and source-record versions.
- Use deterministic code for calculations.
- Provide safe failure behavior.
- Never allow model output to directly mutate facts or approved plans.
- Maintain extraction regression evaluations.
- Treat prompt injection as a security concern.

Integration adapters should support authentication/revocation, encrypted tokens, idempotency, bounded retries, rate limits, checkpoints, provider revision reconciliation, missing-data states, disconnect/deletion, and private-data-free fixtures. Never claim unverified Garmin access or metrics.

## Testing and Verification

Run the smallest relevant checks during work and all applicable checks before closure:

- Unit and property-based tests.
- Database and migration tests.
- API and integration tests.
- Linting, formatting, static typing, and frontend builds.
- End-to-end tests.
- Security and authorization tests.
- Accessibility and visual checks.
- LLM regression evaluations.
- PDF render inspection.
- Backup and isolated restore tests.

Never claim a check passed unless it actually ran successfully. If a required check cannot run, record why and the risk; do not close the issue when that check is part of acceptance.

Do not weaken assertions, delete meaningful tests, or suppress errors only to obtain a passing result.

For UI changes, verify mobile and desktop layouts, keyboard navigation, focus, semantic labels, accessible chart alternatives, and visual distinction among facts, plans, and AI. For reports, render representative documents and inspect clipping, page breaks, tables, labels, timezone, provenance, print legibility, and AI-default-off behavior.

## Database Migrations

For schema changes:

- Create a reviewed migration.
- Test upgrade from the prior supported schema.
- Preserve facts and audit history.
- Avoid destructive behavior where possible.
- Document backfills and make them restartable/idempotent.
- Consider rollback or forward-fix behavior.
- Never run production migrations without explicit deployment authorization.

## Observability

For background jobs, integrations, and critical workflows, add appropriate redacted logs, correlation IDs, success/failure metrics, retry/dead-letter behavior, last-success timestamps, queue age, user-visible health, and actionable alerts. Never expose health information through logs, metric labels, health endpoints, traces, or error reporting.

## Documentation and ADRs

Update documentation when work changes setup, environment variables, schemas, migrations, APIs, safety behavior, integrations, backup/restore, deployment, or user workflows.

Create an ADR for material decisions involving architecture, persistence, security/privacy, model or document ingestion, external integrations, deployment topology, backup/recovery, or medical-safety behavior. Do not create ADRs for routine details.

## Definition of Ready

An issue is ready when:

- Its outcome and acceptance criteria are clear.
- Required dependencies are satisfied.
- Safety, privacy, security, data, and migration effects are identified.
- Unknowns capable of invalidating the work have completed spikes.
- Required external access is available, or the issue can be completed without it.

If not ready, update or split the issue instead of guessing about a high-impact decision.

## Definition of Done

An issue is done only when:

- All acceptance criteria pass.
- Relevant normal, failure, safety, and privacy paths are tested.
- Required migrations, API/UI contracts, accessibility, observability, recovery, and documentation are complete.
- Evidence is recorded in Beads.
- Deferred work has linked issues.
- No secrets or private health data were introduced.
- The work is committed when autonomous commit authority applies.
- Beads accurately reflects completion.

## Session Completion

Before ending an autonomous session:

1. Run applicable quality gates.
2. Review the working tree.
3. Update every claimed Beads issue accurately.
4. Close only fully completed issues.
5. Create linked follow-ups and document blockers.
6. Commit completed work when authorized.
7. Synchronize Beads and push when authorized and safe.
8. Confirm local and remote status.
9. Summarize completed/in-progress issues, changes, verification, commits, sync/push status, and blockers.

Do not leave an issue in progress when no implementation was performed unless it contains an accurate blocker and handoff note.

# Private chatbot release verification

This checklist maps the private analytical chatbot's hardening requirements
(ADR-0025 as amended by [ADR-0036](adr/0036-local-model-analytical-chat.md)) to
executable checks. All fixtures and recorded model outputs are synthetic; no owner
health text is present in the repository or baseline.

## Safety and failure behavior

- `tests/unit/test_chat_orchestration.py` exercises the native tool-calling loop,
  numeric validation against tool results (including clock times, thousands
  separators, and signs), one repair turn, medication-guidance rejection, refusals,
  injected tool text, tool errors returned for repair, context trimming, turn and
  whole-run bounds, model unavailability and timeouts, unconfigured analysis, and
  source-fingerprint staleness.
- `tests/unit/test_ollama_chat.py` and `tests/unit/test_ollama_failure_modes.py` verify
  typed model outcomes, tool-call validation, and that thinking text is never returned,
  logged, or replayed.
- `evals/chatbot/gold-v3.json` and `evals/chatbot/baseline-v3.json` cover clock-time
  averaging, medication-guidance refusal, retrieved prompt injection, wearable metrics
  around symptoms, and missing-day step averages on the selected local model, pinned by
  immutable digest.

## Ownership, least privilege, and privacy

- `tests/integration/test_analytics_views.py` and `tests/integration/test_analysis_query.py`
  verify that model-authored queries run only as the view-only analyst roles: base
  tables, identity, AI state, and opt-in text are denied at the database, writes fail
  even when read-write is forced, multiple statements are refused by PostgreSQL, and the
  statement timeout applies.
- `tests/unit/test_analysis_query.py` and `tests/unit/test_analysis_helpers.py` verify SQL
  validation, tool argument contracts, and catalog exposure.
- `tests/integration/test_api_safety.py` verifies owner-scoped conversation lifecycle,
  bounded context, observable rate limiting, redacted audit events, deletion cascade,
  and that chat cannot mutate recorded facts or physician-approved plans.
- `tests/integration/test_schema_privileges.py` verifies AI and backup role grants for
  chatbot tables and that modeled exposure runs through the restricted AI role.
- `tests/unit/test_private_export_chat.py` verifies chatbot export remains excluded
  unless both AI and sensitive content are explicitly requested.

## Bounds, accessibility, and provenance

- Context, turn, tool-call, tool-result, output-token, whole-run, and per-turn time
  bounds are enforced in `healthcurve.chat.orchestration` and covered by unit tests.
- Each completed answer stores the tool calls it used, including query text and result
  fingerprints; stale-answer checks replay them through the analyst roles.
- `frontend/src/pages/ChatPage.test.tsx` covers keyboard submission, visible async and
  failure states, provenance disclosure, cancellation, and history expansion. The
  responsive shell and serious/critical axe-core checks are covered by
  `frontend/src/Accessibility.test.tsx`.

## Release commands

```bash
uv run pytest tests/unit/test_ollama_chat.py tests/unit/test_ollama_failure_modes.py \
  tests/unit/test_chat_orchestration.py tests/unit/test_analysis_query.py \
  tests/unit/test_analysis_helpers.py tests/unit/test_private_export_chat.py -q
uv run pytest tests/integration/test_analytics_views.py \
  tests/integration/test_analysis_query.py tests/integration/test_analysis_helpers.py -q
uv run pytest tests/integration/test_api_safety.py -q
uv run pytest tests/integration/test_schema_privileges.py -q
uv run python scripts/evaluate_chatbot.py
cd frontend && npm run check
```

Recording a new selected-model baseline is a deliberate local-only release action and
uses the command documented in `docs/chatbot-evaluation.md`. A failed safety case must
be fixed; weakening a validator or fixture to accept unsafe output is not release
evidence.

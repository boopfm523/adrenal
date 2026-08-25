# Private chatbot release verification

This checklist is the release evidence for `hc-met.6`. It maps the private chatbot's
hardening requirements to executable checks. All fixtures and recorded model outputs
are synthetic; no owner health text is present in the repository or baseline.

## Safety and failure behavior

- `tests/unit/test_chat_orchestration.py` exercises successful read/answer flow,
  refusal without a health-data read, unknown and duplicate tool calls, unsupported
  numeric claims, injected retrieved text, model unavailability, timeout, malformed
  planning, malformed answers, immutable model identity, and source-fingerprint
  staleness.
- `tests/unit/test_ollama_failure_modes.py` verifies typed unavailable, timeout, HTTP,
  empty, array, and malformed JSON outcomes. It also verifies strict user/system
  separation, schema forwarding, bounded generation options, fenced-object recovery,
  and one bounded recovery from Ollama's incomplete `think:false` response.
- `evals/chatbot/gold-v2.json` and `evals/chatbot/baseline-qwen3-30b.json` cover wearable
  missingness, medication-guidance refusal, retrieved prompt injection, a preceding-
  hours unwell question, and long-term event-centered comparison on the selected
  private model. The baseline pins `qwen3:30b` by immutable digest.

## Ownership, least privilege, and privacy

- `tests/integration/test_chat_tools.py` verifies owner isolation on every domain tool
  and PostgreSQL read-only transactions for tool execution.
- `tests/integration/test_api_safety.py` verifies owner-scoped conversation lifecycle,
  bounded context, observable rate limiting, redacted audit events, deletion cascade,
  and that chat cannot mutate recorded facts or physician-approved plans.
- `tests/integration/test_schema_privileges.py` verifies the AI and backup database-role
  grants for chatbot tables.
- `tests/unit/test_private_export_chat.py` verifies chatbot export remains excluded
  unless both AI and sensitive content are explicitly requested.
- Backup and restore use the schema-complete database archive path; chatbot records are
  in the covered `ai` schema and are restored under the same role-boundary checks.

## Bounds, accessibility, and provenance

- Context character, turn, planning-round, tool-call, tool-result, output-token, whole
  run, and per-model time bounds are enforced in `healthcurve.chat` and covered by unit
  and API tests.
- `frontend/src/pages/ChatPage.test.tsx` covers keyboard submission, visible async and
  failure states, provenance disclosure, cancellation, and individual/all-turn history
  expansion. The responsive shell and serious/critical axe-core checks are covered by
  `frontend/src/Accessibility.test.tsx`.
- Full tool-result hashes remain in persisted source manifests and fingerprints. Short
  model-facing citation aliases are validated against those immutable results; aliases
  never replace stored provenance.

## Release commands

```bash
uv run pytest tests/unit/test_ollama_failure_modes.py \
  tests/unit/test_chat_orchestration.py tests/unit/test_chat_tools.py \
  tests/unit/test_private_export_chat.py -q
uv run pytest tests/integration/test_chat_tools.py -q
uv run pytest tests/integration/test_api_safety.py -q
uv run pytest tests/integration/test_schema_privileges.py -q
uv run python scripts/evaluate_chatbot.py
cd frontend && npm run check
```

Recording a new selected-model baseline is a deliberate local-only release action and
uses the command documented in `docs/chatbot-evaluation.md`. A failed safety case must
be fixed; weakening a validator or fixture to accept unsafe output is not release
evidence.

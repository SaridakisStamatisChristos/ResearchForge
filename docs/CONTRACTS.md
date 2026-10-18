# Versioned contracts

The source of truth is `src/researchforge/domain/models.py`. Committed JSON schemas are
built with `python tools/generate_schemas.py`; `--check` rejects drift. All models reject
unknown fields, invalid enum values, missing required fields and mutation after creation.

- `CampaignManifest` describes intent, roles and bounded budgets; path and Git-OID resolution
  are **not** performed at PR01 and will be required at PR03.
- `EvidenceEvent` describes a prospective append-only chain event; actual hash linking and
  durable insertions are **not** implemented until PR02.
- `EvaluationResult` validates an evaluator-produced result's *shape*; it does not prove
  process execution or correctness. Identity checks and parser provenance come in PR04.
- `ResponseEnvelope` gives `schema_version`, `operation_id`, `status`, `data`, `errors`,
  and `warnings`. Reserved exit statuses: 0 success, 2 invalid input, 3 policy refusal,
  4 external failure, 5 unknown, 6 ledger corruption.

Do not treat contract validation alone as scientifically verified evidence.

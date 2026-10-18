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

## PR02 storage semantics

`EventLedger` publishes canonical payload bytes to `FileSystemCAS` before a transaction
that appends the event, refusing stale expected sequences. Its local event chain is
replayed offline by `verify`, checking expected sequence, event hash and blob digest.
A SQLite or blob mutation is detected; this is **not tamper proof** against full database
and blob rewriting by the machine owner. Crash-injected precommit exceptions leave zero
committed phantom events and may leave identifiable orphan blobs. No automatic cleanup.

## PR03 manifest.lock.json

`FrozenManifest` is a typed, versioned lock derived from `CampaignManifest` and exact bytes
of the original evaluator/split inputs. The SHA-256 lock bytes are bound by a
`CAMPAIGN_FROZEN` event. Freezing is idempotent for unchanged inputs and recovers a
lock-only crash without claiming evaluator success. Changes to frozen inputs are refused.

## PR04 executor and evidence

`rf run evaluate` requires `--approve-trusted-local`. Only `dev` and
`qualification` can run, never `final`. Each attempt has an immutable
`ATTEMPT_STARTED` event with exact command, worktree and frozen hashes, followed
by a terminal `ATTEMPT_SUCCEEDED`, `ATTEMPT_FAILED`, `ATTEMPT_TIMED_OUT`, or
`ATTEMPT_UNKNOWN`. The terminal payload contains raw stdout/stderr CAS references
and a validated result only on success. An orphan start requires explicit
`rf run recover --mark-unknown`. Unknown process outcomes are not retried
automatically. Success proves only that the trusted evaluator produced a
contract-matching result in this local environment.

## PR05 lifecycle and qualification

`HYPOTHESIS_PROPOSED` binds a human-authored falsifiable specification; the
`CANDIDATE_REGISTERED` event pins a full candidate commit/tree to an existing
hypothesis and cannot be reassigned. PR04 `ATTEMPT_STARTED` events now carry a
`candidate_id` (baseline is `baseline`) and the exact corresponding candidate
commit/tree. `QUALIFICATION_DECIDED` stores immutable, policy-fingerprinted
`QUALIFIED`, `REJECTED` or `INCONCLUSIVE` decisions and references the source
terminal-event hashes. The latest attempt governs each candidate/split: failure,
timeout or unknown does not silently fall back to an earlier valid score.

The deterministic `strict-improvement-v1` policy requires successful, verified
baseline and candidate results on both `dev` and `qualification`, exploratory
development improvement, frozen direction and a qualification improvement meeting
both `minimum_absolute_improvement` and the `allow_equal` rule. A decisive
failure produces `REJECTED`; missing verified evidence produces `INCONCLUSIVE`.
Subsequent evidence appends a new decision with a supersession link. Decisions
never grant Git merge authority and do not constitute independent final evidence.

## PR06 final-state contract

`FINAL_PLAN_FROZEN` permanently closes candidate selection; its immutable
payload binds an already-committed `QUALIFICATION_DECIDED` event, candidate and
baseline exact commit/tree, frozen final split digest, policy and threshold.
`FINAL_EVALUATION_STARTED` is a one-shot consumption event. It precedes both
final `ATTEMPT_STARTED` events (baseline and nominated candidate) and forbids
retry after interruption or an unknown outcome. `FINAL_EVALUATED` contains
`FINAL_SUPPORTED`, `FINAL_NOT_SUPPORTED`, or `FINAL_INCONCLUSIVE` and
terminal event references; `rf verify` rebuilds the result from independently
reparsed CAS stdout and validates the saved conclusion. When process evidence is
incomplete, return `FINAL_UNKNOWN` with no automatic second attempt.

`paired_comparison` requires exact matching nonempty case ID maps, finite values,
a declared metric direction and a frozen random seed. It yields a seeded paired
percentile-bootstrap interval, clearly marked exploratory. The v1 aggregate-only
final evaluator has no case-level samples and therefore no manufactured CI.

## PR08 LiteratureForge v0.1

`LiteratureRecord` stores each provider's distinct stable ID, normalized DOI
when syntactically valid, source title/authors/year, raw source item and its
canonical SHA-256. `RetrievalBatch` stores full request provenance, all
`RetrievalPage` hashes and statuses, provider's reported count, retrieved
count, next cursor and explicit completeness. Each provider has typed
`lookup_identifier` and `search` methods.

Provider records are not evidence of scientific accuracy. DOI equality is the
sole automatic identity grouping rule; title matching is not a silent merge.
Source lookup 404 is represented as source-specific `NOT_FOUND`.
`COMPLETE` means requested pagination was exhausted; `PARTIAL` means a
limit or failure truncated retrieval; `UNKNOWN` means initial retrieval
failed or provider payload was unusable. The snapshot archive verifies its
canonical digest and every raw-item digest during offline replay.

Provider endpoints are fixed and requests cannot redirect to user-entered
URLs. Search requires explicit `--allow-network`. Optional `httpx` dependency
is installed with `.[literature]`; no existing campaign state is modified.

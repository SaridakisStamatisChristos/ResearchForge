# ResearchAuditor v0.1 — deterministic claim-evidence checks (roadmap PR09)

Run without network or evaluator execution:

    rf audit claims --input claims.json --campaign /path/to/campaign --format json
    rf audit claims --input claims.json --literature-store /path/to/snapshots --format md

The input JSON is an object with schema_version "1.0" and a "claims" array.
Each claim declares "claim_id", "text", "scope", "kind", "expected",
optional "subject_id", and "evidence" references of the form
{"source":"campaign_event","sha256":"<full-64-digit-event-hash>"} or
{"source":"literature_snapshot","sha256":"<full-64-digit-snapshot-hash>"}.

Supported checks:
- final_status: expected FINAL_SUPPORTED, FINAL_NOT_SUPPORTED or FINAL_INCONCLUSIVE.
  It requires the exact FINAL_EVALUATED event and independently replayed final
  evaluation. SUPPORTED means only that the *predeclared bounded final policy*
  returned the stated status; it does not claim external statistical significance.
- qualification_status: expected QUALIFIED, REJECTED or INCONCLUSIVE,
  subject_id is the candidate ID. The latest decision's exact event hash is
  required; this can establish OBSERVED, not confirmatory support.
- literature_doi: expected normalized DOI and optional provider-id subject_id.
  Requires an independently hash-verified PR08 retrieval archive. An exact
  DOI hit is OBSERVED source metadata, not validation of a paper or global
  discoverability. No matching item gives INCONCLUSIVE, never proof of absence.

Each result lists used/missing hashes and detected contradictions, with statuses
OBSERVED, SUPPORTED, CONTRADICTED, INCONCLUSIVE, UNVERIFIED and NOT_EVALUATED.
Unknown evidence IDs fail closed; duplicate claim IDs, references, malformed
JSON, NaN/Inf, invalid DOI or wrong link types are rejected. Corrupt campaign
ledgers return exit code 6. Input errors return 2. A read-only audit reporting
a contradiction may return 0: command success is not claim success.

No assertions about scholarly novelty, peer-review acceptance, statistical
generalization, causal effectiveness, or automatic scientific truth are made.
The audit is not an authorization to merge or publish research. Full control
of local SQLite and blob storage permits malicious history rewriting;
cryptographic external checkpoints are not implemented.

No ResearchForge or upstream third-party code is copied by this module. It
does not assign a repository license or alter existing personal plugins.

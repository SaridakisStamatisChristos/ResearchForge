# ResearchForge architecture (PR01)

RF-ARCH-001 governs the project. Target: local-first Python 3.12+ modular monolith.
Layer map: `domain` holds immutable validated contracts; future `application` contains
workflows and gates; `ports` define side-effect interfaces; `adapters` implement SQLite,
CAS, Git and trusted subprocesses; `cli` calls application services.

PR01 implements only contracts and the CLI shell. There is no ledger, evaluator, sandbox,
data sealing or reproducibility claim yet. The engine's eventual source of truth must be
validated execution evidence rather than model-generated scores.

PR02 implements the append-only ledger and SHA-256 CAS. PR03 adds a read-only validation
and explicit freeze service. The immutable lock contains complete source Git OIDs, evaluator
Python source hashes, dataset split hashes/counts, policy and environment fingerprints.
Only trusted `file://` repositories are accepted. Split JSON input uses `{"cases":[{"id":"..."}]}`,
with globally unique IDs across roles; symlink escapes fail closed.

PR04 adds an explicit-approval trusted-local runner: detached Git worktrees at a
frozen full commit OID, bounded shell-free subprocesses, strict parsed results, CAS
raw output references and append-only start/terminal attempt events. The
`final` split remains sealed. A per-campaign OS file lock serializes execution
and explicit recovery; an interrupted `ATTEMPT_STARTED` is never silently replayed.
This is not a security sandbox and makes no statistical claims.

PR05: `application/qualification.py` records hypotheses, exact candidate Git
identities, and deterministic decisions as immutable event payloads. It uses the
PR04 campaign OS lock to prevent concurrent registration/qualification races,
and replays source event hashes and immutable CAS stdout before accepting a
measured number. The frozen qualification gate is a repeated selection stage;
the `final` split stays inaccessible. No autonomous hypothesis generator or
statistical superiority claim is implemented in this PR.

PR06 adds a no-LLM `FinalService` as the only entry point into the sealed final
role; a stored final-plan event is the irreversible selection/evaluation boundary.
Final completion is recomputed from raw CAS artifacts and independent v1 parser
checks. The final plan stores both comparison arms and consumes a one-shot right
at execution start. Runner and qualification gate direct post-nomination search.
The separate statistics module provides seeded paired-sample percentile bootstrap
without inferring per-case distributions from aggregate metrics.

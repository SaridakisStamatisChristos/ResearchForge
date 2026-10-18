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

# ResearchForge architecture (PR01)

RF-ARCH-001 governs the project. Target: local-first Python 3.12+ modular monolith.
Layer map: `domain` holds immutable validated contracts; future `application` contains
workflows and gates; `ports` define side-effect interfaces; `adapters` implement SQLite,
CAS, Git and trusted subprocesses; `cli` calls application services.

PR01 implements only contracts and the CLI shell. There is no ledger, evaluator, sandbox,
data sealing or reproducibility claim yet. The engine's eventual source of truth must be
validated execution evidence rather than model-generated scores.

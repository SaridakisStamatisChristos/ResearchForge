# PR07 MVP acceptance / boundaries

**Scope:** only PR01–PR07, no literature API, third-party skill import, LLM decision,
untrusted-code sandbox, repository auto-merge, or general publication claim.

The `rf demo run --offline --output NEW_DIRECTORY` command must:

1. Initialize an isolated local Git repository, then commit baseline, good and overfit
   model versions with separate full OIDs.
2. Build a manifest containing a frozen base commit, evaluator, SHA-256 disjoint
   dev/qualification/final dataset files, metric, seed, threshold and budgets.
3. Run **six real** dev/qualification subprocess evaluations and record validated
   raw output refs and terminal events. Never populate score evidence by hand.
4. Record two human-style falsifiable hypotheses and Git-identified candidates.
   Deterministic replay qualifies `good`, rejects `overfit` and retains the
   rejected decision with evidence IDs.
5. Nominate `good` before **any final subprocess**. Final nomination fixes
   candidate commit, baseline commit, policy, sample digest and comparison.
6. Run the baseline and nominated candidate against final **once each**, recording
   both raw outputs. Verify an aggregate FINAL_SUPPORTED result; do not extrapolate.
7. Replay all blob/event-chain integrity, produce read-only Markdown/JSON reports
   and refuse repeated final evaluation. Preserve output on interrupted execution.
8. Make an independent run in a second clean directory; normalized split/evaluator
   and successful raw-output hashes must match, while timestamps and commit IDs
   may differ.
9. Tampering with a committed CAS blob must cause `rf verify` to fail without
   mutating an already emitted report. Neither `rf demo` nor skill export may
   overwrite existing user directories.
10. Validate a bundled portable SKILL.md and only export it on explicit user
    request. Do not silently modify any installed personal plugin.

## Invariant-to-test map

| Invariant | Concrete coverage |
|---|---|
| I-01 traceability | `tests/integration/test_demo.py` real attempt event refs and final stdout hash |
| I-02 actual executed scores | Eight real `ATTEMPT_STARTED` and terminal records; no pre-seeded score |
| I-03 Git identity | Worktree-based runner tests; demo baseline/good/overfit full OIDs |
| I-04 dev/qualification/final split | Role-specific inputs, event ordering assertions |
| I-05 unknown failure semantics | `tests/recovery/test_crashes.py`, runner unknown, final one-shot tests |
| I-06 append-only evidence | EventLedger CAS/chain unit and integration tests |
| I-07 hard budgets | PR04/PR05 attempt and candidate caps, final-arm reservation |
| I-08 deterministic decisions | Identical good/rejected decisions across fresh offline reruns |
| I-09 no silent side effects | Exclusive output path, no origin checkout mutation, explicit export |
| I-10 license/provenance | No external code imported; license deliberately undecided |

## Verification and limitations

GitHub Actions runs Ruff, Ruff-format, mypy, JSON-schema drift, pytest, CLI
smoke and a **second real** offline demonstration on Python 3.12 and 3.13.
Tests cover a corrupted CAS blob and repeatable source/result hashes. The repo
does not yet have exhaustive fault injection for arbitrary SIGKILL timing,
network isolation for untrusted processes, reproducible dependency lockfiles,
external benchmarking, formal statistical sampling designs, or commercial
readiness. Passing toy fixtures is necessary for the MVP but not sufficient to
make a scientific novelty or superiority claim.

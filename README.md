# ResearchForge

Local-first research experimentation toolkit, built independently from RF-ARCH-001.

## Status

**Through PR05:** typed contracts, frozen campaign inputs, append-only evidence/CAS,
trusted-local Git worktree execution, candidate registration and deterministic selection-stage
qualification are implemented. Statistical comparison, untouched final evaluation, offline E2E
demo and portable skill remain future PR06–PR07 work. This does not certify scientific claims.

## Quick start

Requires Python 3.12+.

```bash
python -m pip install -e '.[dev]'
rf --version
rf contracts schema --name campaign
rf contracts schema --name evaluator-result
python tools/generate_schemas.py --check
pytest
```

All runtime functions are local. Core use requires no service or API key. Existing projects and
agent plugins are never modified. **No repository license has been selected.**

See `docs/ARCHITECTURE.md`, `docs/CONTRACTS.md` and `docs/roadmap.md`.

> Executing untrusted candidate code using a local subprocess is **not a security sandbox**.
> Later runner phases require explicit trusted-command approval.

## PR02 API: local evidence ledger (no evaluator yet)

```python
from pathlib import Path
from researchforge.application.evidence import EventLedger

ledger = EventLedger(Path("/tmp/rf-campaign"))
event = ledger.append_event("example", 0, "CREATED", "example", {"objective": "demo"})
print(event.event_sha256)
print(ledger.verify("example"))
```

This verifies stored content integrity, **not** that research measurements were executed.

## PR03 local campaign freeze

```bash
rf campaign init --manifest ./examples/my-campaign.json --output /tmp/my-rf-campaign
rf campaign validate --campaign /tmp/my-rf-campaign
rf campaign freeze --campaign /tmp/my-rf-campaign
rf campaign inspect --campaign /tmp/my-rf-campaign --json
```

These commands resolve full local Git OIDs, evaluator Python file digests, disjoint split IDs,
policy and environment fingerprints. The lock cannot be silently overwritten. No evaluator
command is executed by PR03, and no benchmark score is produced.

## PR04 trusted-local evaluator execution

```bash
rf run evaluate --campaign /tmp/my-rf-campaign --split dev --approve-trusted-local
rf run evaluate --campaign /tmp/my-rf-campaign --split qualification --approve-trusted-local
rf run recover --campaign /tmp/my-rf-campaign --mark-unknown
```

**Danger: this is not a sandbox.** Only authorize commands from code you trust to run
on your machine. The runner does not read arbitrary external repository URLs, invoke
a shell, or accept `final` split runs. It creates ResearchForge-owned detached Git
worktrees, pins full commit/tree identities, limits process wall time and captured
stdout/stderr, and stores raw output and immutable attempt records in the local CAS
and ledger. Scripts are executed with a small allowlisted environment; this is
defense in depth, not isolation.

Only a zero-exit, non-truncated, correctly parsed result matching frozen metric,
direction, units, sample count, digest and correctness is recorded as
`ATTEMPT_SUCCEEDED`. An interrupted or ambiguous attempt stays `UNKNOWN`; explicit
recovery records `ATTEMPT_UNKNOWN` without rerunning it. Worktrees are intentionally
retained for audit; automatic cleanup is not implemented. PR05 will add the
hypothesis/candidate qualification state machine and PR06 the sealed final run.

## PR05: candidate and deterministic qualification lifecycle

Use a **full commit OID** in the base manifest *before* freezing. If the manifest
contains the mutable name `HEAD`, advancing the source checkout makes freeze
inspection fail closed as designed.

```bash
rf hypothesis add --campaign /tmp/my-rf-campaign --file hypothesis.json
rf candidate register --campaign /tmp/my-rf-campaign \
  --id candidate-a --hypothesis h-001 --commit <full-commit-oid>
rf run evaluate --campaign /tmp/my-rf-campaign --split dev --approve-trusted-local
rf run evaluate --campaign /tmp/my-rf-campaign --split qualification --approve-trusted-local
rf run evaluate --campaign /tmp/my-rf-campaign --candidate candidate-a \
  --split dev --approve-trusted-local
rf run evaluate --campaign /tmp/my-rf-campaign --candidate candidate-a \
  --split qualification --approve-trusted-local
rf qualify --campaign /tmp/my-rf-campaign --candidate candidate-a
```

`hypothesis.json` must contain `hypothesis_id`, `proposition`, `intervention`,
`falsifier` and optional `parent_id`. Registration pins an immutable Git commit/tree
and does not merge or modify source branches. Every measurement is read back from
successful PR04 subprocess records with original stdout CAS hashes, metric/split
validation and exact candidate identity. Failures and rejected candidates remain in
the append-only ledger. Hypotheses and candidates are separately limited to
`budget.max_hypotheses`; runner attempts/wall-time remain bounded by their frozen
limits. Repeat qualification attempts count as **selection exposure** and cannot be
presented as an untouched test. An `INCONCLUSIVE` decision can be superseded, never
overwritten, when subsequently obtained evidence changes its basis. No final data
is accessed at PR05. **This runner does not isolate untrusted code.**

## PR06: preregistered one-shot final test, replay and statistical summaries

After qualifying a candidate, nominate it **before** any final evaluation:

```bash
rf final nominate --campaign /tmp/my-rf-campaign --candidate candidate-a
rf final inspect --campaign /tmp/my-rf-campaign
rf final evaluate --campaign /tmp/my-rf-campaign --approve-trusted-local
rf verify --campaign /tmp/my-rf-campaign
rf report --campaign /tmp/my-rf-campaign --format md --output /tmp/report.md
```

The nomination locks candidate OID, baseline, final split digest, qualification
evidence, metric direction and threshold. The first final execution start consumes
the one-shot right, even if an interrupted process leaves `FINAL_UNKNOWN`.
Only the authorized final service may launch final evaluators. Development and
qualification evaluation and new candidate/hypothesis registration are refused
after nomination. Final runs execute a baseline and nominated candidate on the
final split using the existing trusted-local runner; their complete raw output
digests and terminal events remain in the evidence ledger. No Git merge occurs.

The resulting `FINAL_SUPPORTED` means only that the declared aggregate metric
threshold was met on this frozen final set. It **does not** establish statistical
or population-level superiority. The evaluator v1 result contains only an
aggregate, so no independent paired case-level confidence interval can be
inferred from the aggregate.

When case-level measurements **are separately available**, use the deterministic
paired-bootstrap interface:

```bash
rf stats paired --file observations.json --seed 42 --resamples 2000
```

`observations.json` contains `baseline` and `candidate` dictionaries keyed
by identical case IDs plus `direction` (`min` or `max`). The interval is an
exploratory percentile bootstrap requiring suitable sampling assumptions; it
does not automatically change the frozen final comparison policy. All reports
replay evidence offline and can be written only to a *new* file. No automatic
network, cloud or model execution is involved.

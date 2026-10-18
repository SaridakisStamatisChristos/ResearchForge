# ResearchForge

Local-first research experimentation toolkit, built independently from RF-ARCH-001.

## Status

**PR01–PR07 MVP implementation:** typed contracts, frozen campaign inputs,
append-only evidence/CAS, trusted-local Git worktree execution, candidate registration,
deterministic qualification, statistical summaries, one-shot final evaluation, real offline
toy demonstration, and a portable skill are implemented. Core operation needs no external
API keys or network connection. This is not a security sandbox, scientific-truth
certification, production-ready service, or a license selection.

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

## PR07: end-to-end offline MVP demonstration

On a clean checkout with Python 3.12+, Git, and package dependencies installed:

```bash
python -m pip install -e '.[dev]'
rf demo run --offline --output /tmp/rf-toy-demo-001
rf verify --campaign /tmp/rf-toy-demo-001/campaign
rf report --campaign /tmp/rf-toy-demo-001/campaign --format md
rf skill validate --name researchforge-experiment
rf skill export --name researchforge-experiment --target /tmp/agent-skills
```

The demo exclusively creates a **new output directory** with a Git repository containing
three actual model commits (baseline, generalizing candidate and development-only overfit),
disjoint frozen dev/qualification/final datasets, an evaluator Python script, immutable
campaign ledger and CAS, and final reports. Eight real shell-free evaluator subprocesses
run on exact detached worktrees. The good candidate must qualify, the overfit candidate
must be rejected, and the one-shot final must agree with its predeclared comparison.

The demo writes `summary.json`, `report.json`, `report.md`; it refuses to overwrite an
existing destination. A second run requires a **different empty path**. Compare stable
evaluator/split SHA-256 and raw result hashes across reruns; ledger hashes, Git commit
metadata, timestamps and output paths need not match. To inspect past outcomes after a
crash, do **not** automatically rerun the same demo directory.

Portable skill metadata is bundled with the installed Python package and mirrored under
`skills/researchforge-experiment/SKILL.md` in the source tree. Validation is an offline
metadata/reference check, **not** an official external standards certification. Export
only writes to an explicitly chosen new destination and refuses overwriting skills.

**Safety:** `--offline` means the built-in demo uses local-only code and Git; it does
not create a sandbox or globally disable networking for arbitrary Python processes.
Never use the trusted-local runner on adversarial code. Final comparisons are
aggregate, single-fixture observations—not generalizable significance tests.

See [MVP acceptance evidence](docs/MVP_ACCEPTANCE.md) for invariant and test coverage.

## PR08 — LiteratureForge v0.1

LiteratureForge is an **independent scholarly metadata retrieval module**. It uses
Crossref DOI lookups and work searches and OpenAlex DOI lookups and work/author
searches, with explicit live network consent:

```bash
python -m pip install -e '.[literature]'
rf literature lookup --doi 10.1038/nphys1170 --sources crossref,openalex --json --allow-network --store /tmp/rf-literature
rf literature search --query "dynamic shortest paths" --sources openalex --limit 20 --allow-network --json --store /tmp/rf-literature
rf literature search --query "Alan Turing" --sources openalex --kind authors --limit 10 --allow-network
rf literature report --search-id HASH_FROM_LOOKUP --store /tmp/rf-literature --format md
```

**No network requests occur without `--allow-network`.** Existing experiment,
qualification and sealed final pipelines remain offline and unchanged. The
`[literature]` extra is optional and core installation remains API-key free.
CI uses `httpx.MockTransport` and has **no live provider tests**.

OpenAlex allows basic anonymous retrieval, but its current API requires a free
API key for cursor paging after the first page. To enable this without putting
credentials in a URL, set `OPENALEX_API_KEY` in your local environment before
running a live command. The adapter uses a bearer header and does not save or
print that header. Without a key, deeper OpenAlex retrieval can return
`PARTIAL` on provider refusal; ResearchForge will not claim it completed.


Retrievals record provider, endpoint and parameters, access timestamp,
individual response SHA-256s, result counts, next cursor, HTTP status, retry
count, Retry-After, failure reason and `COMPLETE`/`PARTIAL`/`UNKNOWN`
status. Searches are bounded to 100 records and 5 pages per provider. DOI
normalization groups matching validated identifiers, but each source's title,
author sequence, date, counts, provider ID and raw item hash remain distinct.
Title similarity never silently merges different records.

Use `--store` to create an immutable, SHA-256-addressed local report snapshot.
An offline `rf literature report` validates snapshot and raw-item hashes
without making requests. A 404 is only **NOT_FOUND in that source at the time
observed**; it is not evidence of absence from other scholarly indexes or Google
Scholar. A successful metadata lookup does not verify a paper's claims,
novelty, peer review or academic correctness.

Bounded retries cover 429/5xx and timeouts. A failed page after earlier
success retains prior observations as `PARTIAL`; failure without observations
is `UNKNOWN`. Incomplete retrievals use process exit code 5 and never
pretend to be complete. Network errors/quotas can still occur on real providers,
and their results have not been empirically checked in CI.

## PR11 — HypothesisForge

`rf hypothesis draft --file design.json --output draft.json` validates a
falsifiable research design without touching campaign state. Optional
`rf hypothesis generate --brief brief.txt --model MODEL
--allow-model-network --output draft.json` requires operator opt-in and an
OpenAI API key, and produces an `AI_PROPOSED_UNVERIFIED` draft, never an
approved result. After reading the draft, admit it with
`rf hypothesis approve --campaign PATH --file draft.json
--expect-sha256 DIGEST --approve-human`. Human approval is recorded in the
append-only ledger; existing frozen metric, attempt-budget and sealed-final
guards remain in force. See `docs/HYPOTHESIS_FORGE.md` for limitations and
a complete walkthrough.

## PR12 — PaperForge offline research manuscript draft

`rf paper draft --input examples/paperforge/paper.example.json --format md`
exports a labeled manuscript inventory without any external API. Exact
`campaign_event` hashes can be supplied with `--campaign` for independently
replayed final-evaluation measurements, while `--literature-store` reads
already archived source-specific metadata. Unsupported claims and incomplete
citations remain explicitly labeled; no invented bibliography or scientific
superiority claims. See `docs/PAPERFORGE.md` for the complete evidence and
CLI contract.

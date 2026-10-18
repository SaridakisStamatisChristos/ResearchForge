# PaperForge PR12 — evidence-closed manuscript export

PaperForge is a deterministic, **offline** manuscript drafting/export layer on
PR06's final evaluator, PR08's immutable literature snapshots, and PR09's
ResearchAuditor. It does not invent results, run experiments, query a provider,
call a model, infer novelty, assess publication readiness, or alter a campaign.

## Run

Requires Python 3.12+ and an installed ResearchForge CLI.

```bash
rf demo run --offline --output /tmp/rf-demo
rf paper draft --input examples/paperforge/paper.example.json \
  --campaign /tmp/rf-demo/campaign --format md \
  --output /tmp/rf-manuscript.md
rf paper draft --input examples/paperforge/paper.example.json \
  --campaign /tmp/rf-demo/campaign --format json \
  --output /tmp/rf-manuscript.json
```

The example intentionally has **no evidence hashes** and therefore produces
explicit incomplete/not-validated annotations rather than faked results. To
generate measured result tables, replace the example's claim with an exact
`FINAL_EVALUATED` event hash from your own verified campaign. Example of
retrieving it read-only:

```python
from pathlib import Path

from researchforge.application.evidence import EventLedger

campaign = Path("/tmp/rf-demo/campaign")
ledger = EventLedger(campaign)
ledger.verify("toy-optimizer-v1")
for event in ledger.read_events("toy-optimizer-v1"):
    if event.event_type in {"FINAL_EVALUATED", "QUALIFICATION_DECIDED"}:
        print(event.event_type, event.subject_id, event.event_sha256)
```

Use a `final_status` claim with `expected` matching the exact
`FINAL_SUPPORTED`, `FINAL_NOT_SUPPORTED` or `FINAL_INCONCLUSIVE` status
and a single `campaign_event` link. The ResearchAuditor independently
replays the final plan and underlying validated evaluator output. The
measurement table includes the frozen metric and direction, baseline and
candidate values, units, case counts, pinned commits, split and manifest
hashes, raw stdout hashes, source event hashes, qualification exposure and
the explicit uncertainty limitation. If no complete final-arm evidence is
present, no measurement table is emitted.

Selection-stage `qualification_status` claims remain **EXPLORATORY_ONLY**
and never create measured final tables. Conflicting, missing, unknown and
unverified claims remain in a labeled missing-evidence section. Operator-
supplied titles/authors/claim prose are never reclassified as semantically
verified scientific conclusions.

For bibliography observations, optionally pass
`--literature-store /path/to/previously-stored-snapshots` and include a
`literature_doi` claim with `expected` set to a validated DOI and a
`literature_snapshot` link to an existing content-addressed snapshot.
Each cited observation carries provider name/ID, raw record SHA-256, snapshot
SHA-256, retrieval timestamp, completeness, and provider-supplied title,
authors, year and URL. There is **no fabricated bibliography** and the
existence of metadata does not verify the cited work's scientific claims.
Partial retrieval is explicitly disclosed. No HTTP request is made.

## Guarantees and boundaries

- Output is JSON or Markdown from the same deterministic evidence report. A
  content SHA-256 covers the report, excluding the digest field itself.
- Input is a strict, versioned, at-most-1-MiB `PaperSpec` with
  `schema_version`, `title`, optional `authors`, and a PR09 `ClaimBatch`.
  Duplicate keys, non-finite JSON, unknown fields, and invalid claims fail.
- File exports use exclusive creation. Existing files are never overwritten.
  Without `--output`, output goes to stdout; no source/campaign write.
- Valid final scores are taken from PR06 replay and PR04's original
  independently validated evaluator stdout, not human-entered numbers.
  Incomplete or unsupported claims are *not findings*.
- All untrusted provider and claim text is Markdown-escaped; external source
  URLs are displayed as source text rather than executed or followed.
- PR06's frozen final can be supported only on its declared dataset and
  threshold. No population-level superiority, significance interval, proof
  of novelty, publication approval or independent peer review is implied.
- Corrupted ledger or broken referenced artifacts fail the export. A missing
  literature snapshot remains marked UNVERIFIED and is not cited.
- This is a local-first report generator, **not** a security sandbox or an
  autonomous researcher. No license or external plugin configuration changes.

## Checks

CI must pass Ruff lint/format, strict mypy, schema drift, full pytest and
the real offline ResearchForge demo on Python 3.12 and 3.13. The PR12 tests
exercise executed final-arm scores, qualitative-vs-confirmatory status
separation, source-specific citations, tampered CAS/archive rejection,
incomplete claims, deterministic output and no-clobber export.

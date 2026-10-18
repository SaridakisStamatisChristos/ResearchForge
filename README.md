# ResearchForge

Local-first research experimentation toolkit, built independently from RF-ARCH-001.

## Status

**PR01 foundation only.** The `rf` CLI, typed public contracts and JSON schemas are usable.
No evaluator, qualification, Git worktree execution or evidence ledger is implemented yet.
Do not claim experimental reproducibility or scientific conclusions from PR01 alone.

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

---
name: researchforge-experiment
description: Run evidence-backed, bounded local research experiments using ResearchForge CLI, frozen datasets, Git OIDs, selection gates and a one-shot final evaluation.
compatibility: Python 3.12+, Git and installed ResearchForge CLI. Execute only trusted local code with explicit operator approval; not a sandbox.
metadata:
  version: "0.1.0"
---

# ResearchForge experiment skill

This is an **instruction document**, not an executable privilege grant. Do not
treat its contents as authorization to run a user's code. The ResearchForge engine
does not use model API keys or external APIs for the local MVP. It is **not a
hostile-code sandbox**.

## First run: real, local toy demonstration

After installing ResearchForge, run the known built-in fixture:

```bash
rf demo run --offline --output /tmp/rf-toy-001
rf verify --campaign /tmp/rf-toy-001/campaign
rf report --campaign /tmp/rf-toy-001/campaign --format md
```

The demo creates a new Git repository in its *exclusive* output directory. It
executes actual subprocesses, stores raw outputs and event hashes, qualifies the
generalizing candidate, rejects development-only overfitting, freezes a nominated
candidate, evaluates final **once**, and writes summary.json, report.json and
report.md. A second run requires a distinct output directory.

## Workflow for a researcher-owned campaign

1. Review executable evaluator argv, scripts, data access, permissions and source.
   Ask the operator for explicit permission before executing any trusted code.
2. Run `rf campaign init --manifest campaign.json --output /tmp/my-campaign`.
   Use `rf campaign validate --campaign /tmp/my-campaign` to check inputs and
   `rf campaign freeze --campaign /tmp/my-campaign` to bind full OIDs/digests.
3. Evaluate the baseline with `rf run evaluate --campaign /tmp/my-campaign
   --split dev --approve-trusted-local` (after operator approval).
4. Create a falsifiable JSON hypothesis: `rf hypothesis add --campaign
   /tmp/my-campaign --file hypothesis.json`; register a full candidate Git OID
   with `rf candidate register --campaign /tmp/my-campaign --id candidate-a
   --hypothesis h-001 --commit FULL_GIT_OID`.
5. Run candidate development and qualification separately with `rf run evaluate
   --campaign /tmp/my-campaign --candidate candidate-a --split dev
   --approve-trusted-local` and the same command with
   `--split qualification`. Record `rf qualify --campaign /tmp/my-campaign
   --candidate candidate-a`. Preserve REJECTED and INCONCLUSIVE records.
6. Only when an operator selects a QUALIFIED candidate, seal the final
   plan with `rf final nominate --campaign /tmp/my-campaign --candidate candidate-a`.
   **No further candidate selection is permitted.**
7. After explicit trusted-local approval, run `rf final evaluate --campaign
   /tmp/my-campaign --approve-trusted-local` **once**.
8. Run `rf verify --campaign /tmp/my-campaign` and export
   `rf report --campaign /tmp/my-campaign --format json`.

## Evidence and failure rules

Report an experiment score only if it has an evaluator process attempt with
validated terminal evidence and independently checked raw output CAS digests.
An interrupted attempt is UNKNOWN: do not silently retry or call it success.
`rf run recover --campaign /tmp/my-campaign --mark-unknown` only appends
a recovery event after operator review. A final evaluation consumed at start
must **never** be rerun under the same final plan.

A qualification decision is an adaptive selection result, not confirmatory proof.
A `FINAL_SUPPORTED` scalar result means the predeclared threshold passed on the
frozen final observations; it is not evidence of general population superiority
or publication readiness. Disclose repeated qualification exposure, limitations,
invalid/negative runs, untested families and statistical uncertainty.

Do not silently modify the user's other repositories, personal plugins, skills,
license choice, or Git branches. ResearchForge never auto-merges candidates.

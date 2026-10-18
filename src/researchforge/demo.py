"""Offline, real-subprocess ResearchForge MVP demonstration.

This code creates a NEW, local-only fixture repository with three actual Git
commits. It never writes to an existing source repository or invokes a network API.
Its subprocess evaluator is a known built-in toy; not an adversarial-code sandbox.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from researchforge.application.campaigns import CampaignService
from researchforge.application.final import FinalService
from researchforge.application.qualification import HypothesisSpec, QualificationService
from researchforge.application.runner import RunnerService
from researchforge.domain.serialization import canonical_json

# Kept self-contained so editable installs and installed wheels behave identically.
TOY_EVALUATOR = """\
import argparse
import hashlib
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--candidate", type=Path, required=True)
parser.add_argument("--split", type=Path, required=True)
args = parser.parse_args()
raw = args.split.read_bytes()
dataset = json.loads(raw)
model = json.loads((args.candidate / "model.json").read_text(encoding="utf-8"))
limit = model["trained_through"]
assert isinstance(limit, int) and not isinstance(limit, bool) and limit >= 0
cases = dataset["cases"]
assert cases and len({row["id"] for row in cases}) == len(cases)
errors = []
for row in cases:
    x = row["x"]
    target = row["target"]
    assert isinstance(x, int) and isinstance(target, int)
    prediction = 2 * x + 1 if x <= limit else x
    errors.append(abs(prediction - target))
result = {
    "schema_version": "1.0",
    "status": "complete",
    "metric": "mean_absolute_error",
    "direction": "min",
    "value": float(sum(errors) / len(errors)),
    "unit": "errors",
    "sample_count": len(cases),
    "samples_sha256": hashlib.sha256(raw).hexdigest(),
    "correctness": {"passed": True, "validator_id": "toy-prediction-check-v1"},
}
print(json.dumps(result, sort_keys=True, separators=(",", ":")))
"""


class DemoError(RuntimeError):
    """A real demonstration step did not meet its expected acceptance criteria."""


def _git(repository: Path, *argv: str) -> str:
    command = ["git", "-C", str(repository), *argv]
    try:
        result = subprocess.run(command, capture_output=True, check=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DemoError(f"Local fixture Git operation failed: {argv[0]}") from exc
    return result.stdout.strip()


def _commit_model(repository: Path, label: str, limit: int) -> str:
    (repository / "model.json").write_bytes(canonical_json({"trained_through": limit}) + b"\n")
    _git(repository, "add", "--", "model.json")
    _git(repository, "-c", "commit.gpgsign=false", "commit", "-qm", label)
    oid = _git(repository, "rev-parse", "HEAD")
    if len(oid) not in (40, 64):
        raise DemoError("Fixture commit did not resolve to a full Git object ID")
    return oid


def _write_cases(directory: Path, role: str, values: tuple[int, int]) -> None:
    rows = [{"id": f"{role}-{x}", "x": x, "target": 2 * x + 1} for x in values]
    (directory / f"{role}.json").write_bytes(canonical_json({"cases": rows}) + b"\n")


def _require_success(result: dict[str, Any], label: str) -> None:
    if result.get("status") != "SUCCEEDED":
        raise DemoError(f"{label} failed: {result.get('status')}, {result.get('reason')}")


def run_demo(output: Path) -> dict[str, Any]:
    """Execute all 8 trusted-local evaluations, with final sealed until nomination.

    Never overwrite a preexisting output directory. An interrupted demo is
    intentionally left intact for inspection rather than silently rerun.
    """
    root = output.absolute()
    root.mkdir(parents=True, exist_ok=False)
    repository = root / "toy-repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "ResearchForge toy fixture")
    _git(repository, "config", "user.email", "toy@invalid.example")
    commits = {
        "baseline": _commit_model(repository, "toy baseline", 0),
        "good": _commit_model(repository, "toy generalizing improvement", 100),
        "overfit": _commit_model(repository, "toy development-only overfit", 2),
    }

    inputs = root / "inputs"
    splits = inputs / "splits"
    splits.mkdir(parents=True)
    (inputs / "evaluator.py").write_text(TOY_EVALUATOR, encoding="utf-8")
    _write_cases(splits, "dev", (1, 2))
    _write_cases(splits, "qualification", (5, 6))
    _write_cases(splits, "final", (8, 9))
    manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "campaign_id": "toy-optimizer-v1",
        "objective": "Reduce mean absolute prediction error without dev-only overfitting",
        "base": {"repository": repository.as_uri(), "commit": commits["baseline"]},
        "metric": {"name": "mean_absolute_error", "direction": "min", "unit": "errors"},
        "evaluation": {
            "argv": [
                sys.executable,
                "evaluator.py",
                "--candidate",
                "{candidate_dir}",
                "--split",
                "{split_file}",
            ],
            "cwd": "{evaluator_dir}",
            "timeout_seconds": 10,
            "max_output_bytes": 65536,
            "result_schema": "evaluator-result-v1",
        },
        "splits": {
            "dev": "splits/dev.json",
            "qualification": "splits/qualification.json",
            "final": "splits/final.json",
        },
        "budget": {
            "max_hypotheses": 2,
            "max_attempts": 8,
            "max_wall_seconds": 100,
        },
        "qualification_policy": {
            "name": "strict-improvement-v1",
            "baseline_required": True,
            "minimum_absolute_improvement": 1.0,
            "correctness_checks": ["result-schema", "sample-count"],
            "allow_equal": False,
        },
        "seed": 1701,
        "execution_trust": "local_trusted_only",
    }
    manifest_path = inputs / "campaign.json"
    manifest_path.write_bytes(canonical_json(manifest) + b"\n")
    campaign = CampaignService.init(manifest_path, root / "campaign")
    frozen = campaign.freeze()
    runner = RunnerService(campaign.root)
    selector = QualificationService(campaign.root)
    for candidate, hypothesis_id in (("good", "h-good"), ("overfit", "h-overfit")):
        selector.add_hypothesis(
            HypothesisSpec(
                hypothesis_id=hypothesis_id,
                proposition=f"The {candidate} model reduces development errors",
                intervention=f"Change trained-through cutoff in {candidate} model",
                falsifier="Frozen qualification gain does not meet prespecified policy",
            )
        )
        selector.register(
            candidate_id=candidate,
            hypothesis_id=hypothesis_id,
            commit=commits[candidate],
        )

    # Final is not evaluated, selected upon, or exposed during the following phase.
    for role in ("dev", "qualification"):
        _require_success(runner.run(role=role, approved=True), f"baseline/{role}")
    for candidate in ("good", "overfit"):
        for role in ("dev", "qualification"):
            _require_success(
                runner.run(role=role, approved=True, candidate_id=candidate),
                f"{candidate}/{role}",
            )
    verdict_good = selector.qualify("good")
    verdict_overfit = selector.qualify("overfit")
    if (
        verdict_good["status"] != "QUALIFIED"
        or verdict_overfit["status"] != "REJECTED"
        or verdict_overfit["reason"] != "QUALIFICATION_THRESHOLD_NOT_MET"
    ):
        raise DemoError("Expected evidence-derived qualification and overfit rejection")
    final = FinalService(campaign.root)
    if final.inspect()["final_status"] != "NOT_NOMINATED":
        raise DemoError("Final data was accessed before nomination")
    final.nominate("good")
    conclusion = final.evaluate(approved=True)
    verified = final.inspect()
    if conclusion["status"] != "FINAL_SUPPORTED" or verified["final_result"] != conclusion:
        raise DemoError("Final aggregate result did not independently replay")
    if _git(repository, "rev-parse", "HEAD") != commits["overfit"]:
        raise DemoError("Tool modified the user's selected repository HEAD")

    (root / "report.json").write_text(
        json.dumps(verified, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    (root / "report.md").write_text(final.report_markdown(), encoding="utf-8")
    summary: dict[str, Any] = {
        "status": "PASS",
        "campaign_path": str(campaign.root),
        "commit_oids": commits,
        "decision_statuses": {"good": verdict_good["status"], "overfit": verdict_overfit["status"]},
        "negative_evidence": {
            "candidate_id": "overfit",
            "reason": verdict_overfit["reason"],
            "decision_events": verdict_overfit["evidence_events"],
        },
        "final_status": verified["final_status"],
        "final_result": conclusion,
        "qualification_exposure": verdict_good["qualification_attempt_exposure"],
        "frozen_evaluator_sha256": frozen.evaluator_source_sha256,
        "frozen_split_sha256": {name: split.sha256 for name, split in frozen.splits.items()},
        "ledger_event_count": verified["ledger"]["event_count"],
        "ledger_chain_head": verified["ledger"]["chain_head"],
        "report_json": str(root / "report.json"),
        "report_md": str(root / "report.md"),
        "limitations": conclusion["limitations"],
    }
    (root / "summary.json").write_bytes(canonical_json(summary) + b"\n")
    return summary

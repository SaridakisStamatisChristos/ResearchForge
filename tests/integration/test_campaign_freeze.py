"""Real Git-backed manifest freeze, drift, overlap and safety gates."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from researchforge.application.campaigns import CampaignService, FrozenStateError, ManifestError
from researchforge.cli import app


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args]).decode().strip()


@pytest.fixture
def source(tmp_path: Path) -> tuple[Path, Path]:
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "ResearchForge Test")
    _git(repository, "config", "user.email", "rf@example.invalid")
    (repository / "README.md").write_text("committed source\n")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-qm", "init")
    spec = tmp_path / "fixture"
    spec.mkdir()
    (spec / "evaluator.py").write_text('print("trusted evaluator")\n')
    (spec / "splits").mkdir()
    for split, ident in (("dev", "D"), ("qualification", "Q"), ("final", "F")):
        (spec / "splits" / f"{split}.json").write_text(
            json.dumps({"cases": [{"id": ident}]})
        )
    campaign = {
        "schema_version": "1.0", "campaign_id": "test-001", "objective": "Minimize errors",
        "base": {"repository": repository.as_uri(), "commit": "HEAD"},
        "metric": {"name": "errors", "direction": "min", "unit": "errors"},
        "evaluation": {
            "argv": ["python", "evaluator.py", "--split", "{split_file}"],
            "cwd": "{evaluator_dir}", "timeout_seconds": 10,
            "max_output_bytes": 10000, "result_schema": "evaluator-result-v1"
        },
        "splits": {role: f"splits/{role}.json" for role in ("dev", "qualification", "final")},
        "budget": {"max_hypotheses": 2, "max_attempts": 4, "max_wall_seconds": 200},
        "qualification_policy": {
            "name": "strict-improvement-v1", "baseline_required": True,
            "minimum_absolute_improvement": 1, "correctness_checks": ["result-schema"],
            "allow_equal": False
        },
        "seed": 1701, "execution_trust": "local_trusted_only"
    }
    manifest = spec / "campaign.json"
    manifest.write_text(json.dumps(campaign))
    return manifest, repository


def test_freeze_once_and_replay(source: tuple[Path, Path], tmp_path: Path) -> None:
    manifest, repository = source
    output = tmp_path / "campaign"
    service = CampaignService.init(manifest, output)
    snapshot = service.validate()
    assert snapshot.repository_commit == _git(repository, "rev-parse", "HEAD")
    assert not service.lock_path.exists()
    first = service.freeze()
    assert first == snapshot
    assert service.freeze() == first
    assert service.inspect()["ledger"]["event_count"] == 1
    assert service.lock_path.exists()
    assert not (repository / ".researchforge").exists()


def test_overlap_is_rejected_without_frozen_event(
    source: tuple[Path, Path], tmp_path: Path
) -> None:
    manifest, _ = source
    q = manifest.parent / "splits" / "qualification.json"
    q.write_text('{"cases":[{"id":"D"}]}')
    service = CampaignService.init(manifest, tmp_path / "campaign")
    with pytest.raises(ManifestError, match="overlap"):
        service.freeze()
    assert not service.lock_path.exists()


def test_drift_after_freeze_is_refused(source: tuple[Path, Path], tmp_path: Path) -> None:
    manifest, _ = source
    service = CampaignService.init(manifest, tmp_path / "campaign")
    service.freeze()
    (manifest.parent / "evaluator.py").write_text("print('changed')\n")
    with pytest.raises(FrozenStateError):
        service.freeze()
    with pytest.raises(FrozenStateError):
        service.inspect()


def test_split_symlink_escape_blocked(source: tuple[Path, Path], tmp_path: Path) -> None:
    manifest, _ = source
    outside = tmp_path / "escape.json"
    outside.write_text('{"cases":[{"id":"F"}]}')
    target = manifest.parent / "splits" / "final.json"
    target.unlink()
    target.symlink_to(outside)
    service = CampaignService.init(manifest, tmp_path / "campaign")
    with pytest.raises(ManifestError, match="symlink escape"):
        service.validate()


def test_cli_real_init_freeze_inspect(source: tuple[Path, Path], tmp_path: Path) -> None:
    manifest, _ = source
    output = tmp_path / "campaign"
    runner = CliRunner()
    assert runner.invoke(app, ["campaign", "init", "--manifest", str(manifest),
                                "--output", str(output)]).exit_code == 0
    valid = runner.invoke(app, ["campaign", "validate", "--campaign", str(output)])
    assert valid.exit_code == 0, valid.output
    frozen = runner.invoke(app, ["campaign", "freeze", "--campaign", str(output)])
    assert frozen.exit_code == 0, frozen.output
    inspected = runner.invoke(app, ["campaign", "inspect", "--campaign", str(output),
                                    "--json"])
    assert inspected.exit_code == 0, inspected.output
    assert json.loads(inspected.output)["data"]["ledger"]["event_count"] == 1


def test_recover_lock_only_crash_state(source: tuple[Path, Path], tmp_path: Path) -> None:
    from researchforge.domain.serialization import canonical_json

    manifest, _ = source
    service = CampaignService.init(manifest, tmp_path / "campaign")
    locked = service.validate()
    service.lock_path.write_bytes(canonical_json(locked.model_dump(mode="json")) + b"\n")
    assert service.freeze() == locked
    assert service.inspect()["ledger"]["event_count"] == 1

"""PR04 integration: actual Git worktrees, subprocesses, identities and recovery."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from researchforge.application.campaigns import CampaignService
from researchforge.application.evidence import EventLedger
from researchforge.application.runner import (
    ExecutionRefused,
    RunnerIntegrityError,
    RunnerService,
    _bounded_process,
    _result,
)
from researchforge.cli import app


def git(root: Path, *argv: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *argv]).decode().strip()


@pytest.fixture
def campaign(tmp_path: Path) -> tuple[RunnerService, Path, Path]:
    repo = tmp_path / "repository"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Fixture")
    git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "README.md").write_text("candidate source")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "initial")
    source = tmp_path / "source"
    source.mkdir()
    (source / "splits").mkdir()
    for role, ident in (("dev", "d"), ("qualification", "q"), ("final", "f")):
        (source / "splits" / f"{role}.json").write_text(
            json.dumps({"cases": [{"id": ident}]})
        )
    (source / "evaluator.py").write_text(
        "import json,sys,hashlib,pathlib\n"
        "d=pathlib.Path(sys.argv[2]).read_bytes()\n"
        "print(json.dumps({'schema_version':'1.0','status':'complete','metric':'errors',"
        "'direction':'min','value':1.0,'unit':'errors','sample_count':1,"
        "'samples_sha256':hashlib.sha256(d).hexdigest(),"
        "'correctness':{'passed':True,'validator_id':'toy-v1'}}))\n"
    )
    manifest = {
        "schema_version": "1.0",
        "campaign_id": "test-run",
        "objective": "Toy",
        "base": {"repository": repo.as_uri(), "commit": "HEAD"},
        "metric": {"name": "errors", "direction": "min", "unit": "errors"},
        "evaluation": {
            "argv": ["python", "evaluator.py", "--split", "{split_file}"],
            "cwd": "{evaluator_dir}",
            "timeout_seconds": 2,
            "max_output_bytes": 4096,
            "result_schema": "evaluator-result-v1",
        },
        "splits": {r: f"splits/{r}.json" for r in ("dev", "qualification", "final")},
        "budget": {"max_hypotheses": 2, "max_attempts": 4, "max_wall_seconds": 10},
        "qualification_policy": {
            "name": "strict-improvement-v1",
            "baseline_required": True,
            "minimum_absolute_improvement": 0,
            "correctness_checks": ["result-schema"],
            "allow_equal": False,
        },
        "seed": 42,
        "execution_trust": "local_trusted_only",
    }
    spec = source / "manifest.json"
    spec.write_text(json.dumps(manifest))
    output = tmp_path / "campaign"
    service = CampaignService.init(spec, output)
    service.freeze()
    return RunnerService(output), source, repo


def terminal(runner: RunnerService) -> tuple[str, dict[str, object]]:
    ledger = EventLedger(runner.campaign.root)
    event = ledger.read_events("test-run")[-1]
    return event.event_type, json.loads(ledger.cas.get(event.payload_ref))


def test_success_and_approval(campaign: tuple[RunnerService, Path, Path]) -> None:
    runner, _, repo = campaign
    with pytest.raises(ExecutionRefused, match="approve"):
        runner.run(role="dev", approved=False)
    with pytest.raises(ExecutionRefused, match="Final"):
        runner.run(role="final", approved=True)
    assert len(EventLedger(runner.campaign.root).read_events("test-run")) == 1
    result = runner.run(role="dev", approved=True)
    assert result["status"] == "SUCCEEDED"
    assert terminal(runner)[0] == "ATTEMPT_SUCCEEDED"
    assert terminal(runner)[1]["validated_result"]["value"] == 1.0
    assert EventLedger(runner.campaign.root).verify("test-run")["event_count"] == 3
    assert git(repo, "rev-parse", "HEAD") == runner.campaign.validate().repository_commit


def test_cli_roundtrip(campaign: tuple[RunnerService, Path, Path]) -> None:
    runner, _, _ = campaign
    cmd = ["run", "evaluate", "--campaign", str(runner.campaign.root), "--split", "dev"]
    assert CliRunner().invoke(app, cmd).exit_code == 3
    result = CliRunner().invoke(app, cmd + ["--approve-trusted-local"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["data"]["status"] == "SUCCEEDED"


def test_frozen_source_drift_refused(campaign: tuple[RunnerService, Path, Path]) -> None:
    runner, source, _ = campaign
    (source / "evaluator.py").write_text("print('changed')\n")
    with pytest.raises(RunnerIntegrityError):
        runner.run(role="dev", approved=True)
    assert len(EventLedger(runner.campaign.root).read_events("test-run")) == 1


def test_interrupted_attempt_unknown(campaign: tuple[RunnerService, Path, Path]) -> None:
    runner, _, _ = campaign
    ledger = EventLedger(runner.campaign.root)
    ledger.append_event("test-run", 1, "ATTEMPT_STARTED", "abandoned", {"role": "dev"})
    with pytest.raises(ExecutionRefused, match="Unresolved"):
        runner.run(role="dev", approved=True)
    with pytest.raises(ExecutionRefused):
        runner.recover_unknown(approved=False)
    assert runner.recover_unknown(approved=True) == ["abandoned"]
    assert runner.recover_unknown(approved=True) == []
    assert terminal(runner)[0] == "ATTEMPT_UNKNOWN"


@pytest.mark.parametrize("issue,expected", [
    ("TIMEOUT", "TIMED_OUT"), ("UNKNOWN", "UNKNOWN"), ("OUTPUT_LIMIT", "FAILED")
])
def test_nonverifiable_attempts_never_score(
    campaign: tuple[RunnerService, Path, Path], issue: str, expected: str
) -> None:
    runner, _, _ = campaign
    with patch("researchforge.application.runner._bounded_process") as proc:
        proc.return_value = (-9, b"{}", b"", issue, 0.1)
        attempt = runner.run(role="dev", approved=True)
    assert attempt["status"] == expected
    assert terminal(runner)[1]["validated_result"] is None


def test_budget(campaign: tuple[RunnerService, Path, Path]) -> None:
    runner, _, _ = campaign
    with patch("researchforge.application.runner._bounded_process") as proc:
        proc.return_value = (1, b"{}", b"failed", None, 0.1)
        for _ in range(4):
            assert runner.run(role="dev", approved=True)["status"] == "FAILED"
    with pytest.raises(ExecutionRefused, match="budget"):
        runner.run(role="dev", approved=True)


def test_wrong_metric_rejected(campaign: tuple[RunnerService, Path, Path]) -> None:
    runner, _, _ = campaign
    split = runner.campaign.validate().splits["dev"]
    bogus = {
        "schema_version": "1.0", "status": "complete", "metric": "forged",
        "direction": "min", "value": 0.0, "unit": "errors", "sample_count": 1,
        "samples_sha256": split.sha256,
        "correctness": {"passed": True, "validator_id": "toy"},
    }
    with patch("researchforge.application.runner._bounded_process") as proc:
        proc.return_value = (0, json.dumps(bogus).encode(), b"", None, 0.1)
        attempt = runner.run(role="dev", approved=True)
    assert attempt["status"] == "FAILED"
    assert terminal(runner)[1]["validated_result"] is None


def test_duplicate_json_key_rejected(campaign: tuple[RunnerService, Path, Path]) -> None:
    runner, _, _ = campaign
    manifest, _, _ = runner.campaign._manifest()
    split = runner.campaign.validate().splits["dev"]
    data = (
        '{"schema_version":"1.0","schema_version":"1.0","status":"complete",'
        '"metric":"errors","direction":"min","unit":"errors","value":1,'
        '"sample_count":1,"samples_sha256":"' + split.sha256 + '",'
        '"correctness":{"passed":true,"validator_id":"toy"}}'
    ).encode()
    with pytest.raises(ValueError, match="Malformed"):
        _result(data, manifest=manifest, expected_hash=split.sha256, count=1)


def test_real_timeout(campaign: tuple[RunnerService, Path, Path]) -> None:
    runner, _, _ = campaign
    code, stdout, stderr, issue, duration = _bounded_process(
        ["python", "-c", "import time; time.sleep(10)"],
        cwd=runner.campaign.root, timeout=1, max_output=1024,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert issue in ("TIMEOUT", "UNKNOWN")
    assert code != 0
    assert not stdout and not stderr
    assert duration < 5


def test_real_output_limit(campaign: tuple[RunnerService, Path, Path]) -> None:
    runner, _, _ = campaign
    _, stdout, stderr, issue, duration = _bounded_process(
        ["python", "-c", "print('x' * 99999)"],
        cwd=runner.campaign.root, timeout=3, max_output=100,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert issue == "OUTPUT_LIMIT"
    assert len(stdout) + len(stderr) <= 100
    assert duration < 3

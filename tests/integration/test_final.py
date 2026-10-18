"""PR06 real-subprocess sealed-final, deterministic replay and statistics."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from researchforge.application.campaigns import CampaignService
from researchforge.application.evidence import EventLedger
from researchforge.application.final import FinalService
from researchforge.application.qualification import HypothesisSpec, QualificationService
from researchforge.application.runner import ExecutionRefused, RunnerIntegrityError, RunnerService
from researchforge.cli import app
from researchforge.statistics import StatisticalInputError, paired_comparison


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args]).decode().strip()


@pytest.fixture
def experiment(tmp_path: Path) -> tuple[QualificationService, RunnerService, FinalService, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "RF Test")
    _git(repo, "config", "user.email", "rf@example.invalid")
    (repo / "README").write_text("baseline")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    baseline = _git(repo, "rev-parse", "HEAD")
    source = tmp_path / "inputs"
    source.mkdir()
    (source / "splits").mkdir()
    for role, ident in (("dev", "d"), ("qualification", "q"), ("final", "f")):
        (source / "splits" / f"{role}.json").write_text(json.dumps({"cases": [{"id": ident}]}))
    (source / "evaluator.py").write_text(
        "import json,os,pathlib,sys,hashlib\n"
        "split=pathlib.Path(sys.argv[2])\n"
        "role=split.stem\n"
        "scores=pathlib.Path(os.environ['RF_WORKTREE'])/'scores.json'\n"
        "value=json.loads(scores.read_text()).get(role,10) if scores.exists() else 10\n"
        "print(json.dumps({'schema_version':'1.0','status':'complete','metric':'errors',"
        "'direction':'min','value':float(value),'unit':'errors','sample_count':1,"
        "'samples_sha256':hashlib.sha256(split.read_bytes()).hexdigest(),"
        "'correctness':{'passed':True,'validator_id':'test-v1'}}))\n"
    )
    manifest = {
        "schema_version": "1.0",
        "campaign_id": "final-fixture",
        "objective": "reduce errors",
        "base": {"repository": repo.as_uri(), "commit": baseline},
        "metric": {"name": "errors", "direction": "min", "unit": "errors"},
        "evaluation": {
            "argv": ["python", "evaluator.py", "--split", "{split_file}"],
            "cwd": "{evaluator_dir}",
            "timeout_seconds": 5,
            "max_output_bytes": 10000,
            "result_schema": "evaluator-result-v1",
        },
        "splits": {name: f"splits/{name}.json" for name in ("dev", "qualification", "final")},
        "budget": {"max_hypotheses": 2, "max_attempts": 8, "max_wall_seconds": 50},
        "qualification_policy": {
            "name": "strict-improvement-v1",
            "baseline_required": True,
            "minimum_absolute_improvement": 1.0,
            "correctness_checks": ["result-schema", "sample-count"],
            "allow_equal": False,
        },
        "seed": 4,
        "execution_trust": "local_trusted_only",
    }
    raw = source / "campaign.json"
    raw.write_text(json.dumps(manifest))
    home = tmp_path / "campaign"
    CampaignService.init(raw, home).freeze()
    (repo / "scores.json").write_text(json.dumps({"dev": 7, "qualification": 8, "final": 8}))
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "good candidate")
    candidate_commit = _git(repo, "rev-parse", "HEAD")
    selection = QualificationService(home)
    selection.add_hypothesis(
        HypothesisSpec(
            hypothesis_id="h1",
            proposition="reduced errors",
            intervention="adjust scores",
            falsifier="qualification fails",
        )
    )
    selection.register(candidate_id="good", hypothesis_id="h1", commit=candidate_commit)
    return selection, RunnerService(home), FinalService(home), repo


def _prepare(q: QualificationService, r: RunnerService) -> None:
    for role in ("dev", "qualification"):
        assert r.run(role=role, approved=True)["status"] == "SUCCEEDED"
        assert r.run(role=role, approved=True, candidate_id="good")["status"] == "SUCCEEDED"
    assert q.qualify("good")["status"] == "QUALIFIED"


def test_real_final_one_shot_sealed_and_report(
    experiment: tuple[QualificationService, RunnerService, FinalService, Path],
    tmp_path: Path,
) -> None:
    selection, runner, final, repo = experiment
    assert final.inspect()["final_status"] == "NOT_NOMINATED"
    with pytest.raises(ExecutionRefused, match="Freeze"):
        final.evaluate(approved=True)
    _prepare(selection, runner)
    original = _git(repo, "rev-parse", "HEAD")
    plan = final.nominate("good")
    assert plan["method"] == "aggregate-strict-improvement-v1"
    assert final.nominate("good") == plan
    assert final.inspect()["final_status"] == "NOMINATED"
    with pytest.raises(ExecutionRefused, match="closed"):
        runner.run(role="dev", approved=True)
    with pytest.raises(ExecutionRefused, match="closed"):
        selection.qualify("good")
    with pytest.raises(ExecutionRefused, match="approval|approve"):
        final.evaluate(approved=False)
    result = final.evaluate(approved=True)
    assert result["status"] == "FINAL_SUPPORTED"
    assert result["measured_improvement"] == 2.0
    assert final.inspect()["final_result"] == result
    with pytest.raises(ExecutionRefused, match="one-shot"):
        final.evaluate(approved=True)
    assert _git(repo, "rev-parse", "HEAD") == original
    events = EventLedger(runner.campaign.root).read_events("final-fixture")
    assert sum(e.event_type == "FINAL_EVALUATION_STARTED" for e in events) == 1
    assert sum(e.event_type == "FINAL_EVALUATED" for e in events) == 1
    md = final.report_markdown()
    assert "FINAL_SUPPORTED" in md and "not establish population superiority" in md
    out = tmp_path / "report.md"
    cli = CliRunner()
    args = [
        "report",
        "--campaign",
        str(runner.campaign.root),
        "--format",
        "md",
        "--output",
        str(out),
    ]
    assert cli.invoke(app, args).exit_code == 0
    assert out.read_text() == md
    assert cli.invoke(app, args).exit_code == 2
    assert cli.invoke(app, ["verify", "--campaign", str(runner.campaign.root)]).exit_code == 0


def test_final_not_nominated_if_missing_selection_evidence(
    experiment: tuple[QualificationService, RunnerService, FinalService, Path],
) -> None:
    selection, runner, final, _ = experiment
    with pytest.raises(ExecutionRefused, match="prequalified"):
        final.nominate("good")
    for role in ("dev", "qualification"):
        runner.run(role=role, approved=True)
        runner.run(role=role, approved=True, candidate_id="good")
    with pytest.raises(ExecutionRefused, match="Record qualification"):
        final.nominate("good")
    assert selection.decide("good")["status"] == "QUALIFIED"


def test_crash_after_final_start_is_not_retried(
    experiment: tuple[QualificationService, RunnerService, FinalService, Path],
) -> None:
    q, r, final, _ = experiment
    _prepare(q, r)
    final.nominate("good")
    with patch.object(final.runner, "_run_locked", side_effect=SystemExit(9)):
        with pytest.raises(SystemExit):
            final.evaluate(approved=True)
    assert final.inspect()["final_status"] == "FINAL_UNKNOWN"
    with pytest.raises(ExecutionRefused, match="one-shot"):
        final.evaluate(approved=True)


def test_forged_final_conclusion_rejected(
    experiment: tuple[QualificationService, RunnerService, FinalService, Path],
) -> None:
    q, r, final, _ = experiment
    _prepare(q, r)
    final.nominate("good")
    final.evaluate(approved=True)
    ledger = EventLedger(r.campaign.root)
    final._event(
        ledger,
        "final-fixture",
        "FINAL_EVALUATED",
        "good",
        {"status": "FINAL_SUPPORTED", "measured_improvement": 12345},
    )
    with pytest.raises(RunnerIntegrityError, match="duplicate final conclusion"):
        final.inspect()


@pytest.mark.parametrize(
    "baseline,candidate",
    [
        ({"b": 10.0, "a": 10.0}, {"a": 8.0, "b": 8.0}),
        ({"z": 10.0, "a": 10.0}, {"z": 8.0, "a": 8.0}),
    ],
)
def test_paired_bootstrap_deterministic(
    baseline: dict[str, float], candidate: dict[str, float]
) -> None:
    result = paired_comparison(baseline, candidate, direction="min", seed=17, resamples=200)
    assert result == paired_comparison(candidate, baseline, direction="max", seed=17, resamples=200)
    assert result["estimated_improvement"] == 2.0
    assert result["interval_lower"] == result["interval_upper"] == 2.0
    assert result["status"] == "POSITIVE_CI"


def test_one_case_is_inconclusive() -> None:
    result = paired_comparison({"a": 10.0}, {"a": 4.0}, direction="min", seed=5)
    assert result["status"] == "INCONCLUSIVE"
    assert result["interval_lower"] is None


@pytest.mark.parametrize(
    "baseline,candidate",
    [
        ({"a": float("nan")}, {"a": 1.0}),
        ({"a": 1.0}, {"b": 1.0}),
        ({"a": float("inf")}, {"a": 1.0}),
        ({"a": True}, {"a": 1.0}),
        ({}, {}),
    ],
)
def test_paired_input_refused(baseline: dict[str, float], candidate: dict[str, float]) -> None:
    with pytest.raises(StatisticalInputError):
        paired_comparison(baseline, candidate, direction="min", seed=0)


def test_stats_cli(tmp_path: Path) -> None:
    path = tmp_path / "observations.json"
    path.write_text(
        json.dumps(
            {
                "baseline": {"a": 12, "b": 13, "c": 15},
                "candidate": {"a": 10, "b": 11, "c": 12},
                "direction": "min",
            }
        )
    )
    res = CliRunner().invoke(
        app, ["stats", "paired", "--file", str(path), "--seed", "4", "--resamples", "200"]
    )
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["data"]["observation_count"] == 3


def test_final_plan_refuses_forged_threshold(
    experiment: tuple[QualificationService, RunnerService, FinalService, Path],
) -> None:
    selection, runner, final, _ = experiment
    _prepare(selection, runner)
    original = FinalService._event

    def forge(
        ledger: EventLedger, camp: str, kind: str, ident: str, payload: dict[str, object]
    ) -> object:
        if kind == "FINAL_PLAN_FROZEN":
            payload = {**payload, "threshold": 0.0}
        return original(ledger, camp, kind, ident, payload)

    with patch.object(FinalService, "_event", side_effect=forge):
        final.nominate("good")
    with pytest.raises(RunnerIntegrityError, match="policy"):
        final.inspect()


def test_stats_cli_refuses_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"baseline":{"same":1,"same":2},"candidate":{"same":1},"direction":"min"}')
    result = CliRunner().invoke(app, ["stats", "paired", "--file", str(path), "--seed", "23"])
    assert result.exit_code == 2

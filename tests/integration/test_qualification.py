"""Real executed dev/qualification selection, candidate budget and negative evidence."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from researchforge.application.campaigns import CampaignService
from researchforge.application.evidence import EventLedger
from researchforge.application.qualification import (
    HypothesisSpec,
    QualificationService,
)
from researchforge.application.runner import ExecutionRefused, RunnerIntegrityError, RunnerService
from researchforge.cli import app
from researchforge.hypothesis_forge import ResearchDesign, assemble_draft


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args]).decode().strip()


@pytest.fixture
def research(tmp_path: Path) -> tuple[QualificationService, RunnerService, Path, dict[str, str]]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "RF Test")
    git(repo, "config", "user.email", "rf@example.invalid")
    (repo / "README.md").write_text("source\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "baseline")
    baseline = git(repo, "rev-parse", "HEAD")
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "splits").mkdir()
    for role, ident in (("dev", "d"), ("qualification", "q"), ("final", "f")):
        (inputs / "splits" / f"{role}.json").write_text(json.dumps({"cases": [{"id": ident}]}))
    (inputs / "evaluator.py").write_text(
        "import json,os,pathlib,sys,hashlib\n"
        "split=pathlib.Path(sys.argv[2])\n"
        "role=split.stem\n"
        "file=pathlib.Path(os.environ['RF_WORKTREE'])/'scores.json'\n"
        "scores=json.loads(file.read_text()) if file.exists() else {}\n"
        "value=scores.get(role,10)\n"
        "print(json.dumps({'schema_version':'1.0','status':'complete','metric':'errors',"
        "'direction':'min','value':float(value),'unit':'errors','sample_count':1,"
        "'samples_sha256':hashlib.sha256(split.read_bytes()).hexdigest(),"
        "'correctness':{'passed':True,'validator_id':'test-v1'}}))\n"
    )
    manifest = {
        "schema_version": "1.0",
        "campaign_id": "selection-001",
        "objective": "Reduce errors",
        "base": {"repository": repo.as_uri(), "commit": baseline},
        "metric": {"name": "errors", "direction": "min", "unit": "errors"},
        "evaluation": {
            "argv": ["python", "evaluator.py", "--split", "{split_file}"],
            "cwd": "{evaluator_dir}",
            "timeout_seconds": 5,
            "max_output_bytes": 10000,
            "result_schema": "evaluator-result-v1",
        },
        "splits": {role: f"splits/{role}.json" for role in ("dev", "qualification", "final")},
        "budget": {"max_hypotheses": 2, "max_attempts": 8, "max_wall_seconds": 40},
        "qualification_policy": {
            "name": "strict-improvement-v1",
            "baseline_required": True,
            "minimum_absolute_improvement": 1.0,
            "correctness_checks": ["result-schema", "sample-count"],
            "allow_equal": False,
        },
        "seed": 23,
        "execution_trust": "local_trusted_only",
    }
    src = inputs / "campaign.json"
    src.write_text(json.dumps(manifest))
    folder = tmp_path / "campaign"
    CampaignService.init(src, folder).freeze()
    commits: dict[str, str] = {}
    for name, scores in (
        ("good", {"dev": 7, "qualification": 8}),
        ("overfit", {"dev": 7, "qualification": 13}),
    ):
        (repo / "scores.json").write_text(json.dumps(scores))
        git(repo, "add", "scores.json")
        git(repo, "commit", "-qm", name)
        commits[name] = git(repo, "rev-parse", "HEAD")
    return QualificationService(folder), RunnerService(folder), repo, commits


def add(service: QualificationService, hyp_id: str) -> HypothesisSpec:
    spec = HypothesisSpec(
        hypothesis_id=hyp_id,
        proposition="This change decreases errors",
        intervention="Change scoring code",
        falsifier="Fails strict qualification threshold",
    )
    assert service.add_hypothesis(spec)["hypothesis_id"] == hyp_id
    return spec


def test_real_good_and_overfit_rejected(
    research: tuple[QualificationService, RunnerService, Path, dict[str, str]],
) -> None:
    service, runner, repo, commits = research
    add(service, "h1")
    add(service, "h2")
    service.register(candidate_id="good", hypothesis_id="h1", commit=commits["good"])
    service.register(candidate_id="overfit", hypothesis_id="h2", commit=commits["overfit"])
    for role in ("dev", "qualification"):
        assert runner.run(role=role, approved=True)["status"] == "SUCCEEDED"
    for candidate in ("good", "overfit"):
        for role in ("dev", "qualification"):
            assert (
                runner.run(role=role, approved=True, candidate_id=candidate)["status"]
                == "SUCCEEDED"
            )
    good = service.qualify("good")
    bad = service.qualify("overfit")
    assert good["status"] == "QUALIFIED" and good["qualified_improvement"] == 2.0
    assert bad["status"] == "REJECTED" and bad["reason"] == "QUALIFICATION_THRESHOLD_NOT_MET"
    assert good["qualification_attempt_exposure"] == 2
    assert service.qualify("good") == good
    ledger = EventLedger(runner.campaign.root)
    assert [e.event_type for e in ledger.read_events("selection-001")].count(
        "QUALIFICATION_DECIDED"
    ) == 2
    assert ledger.verify("selection-001")["event_count"] == 19
    assert not any(
        event.event_type.startswith("FINAL_") for event in ledger.read_events("selection-001")
    )
    assert git(repo, "rev-parse", "HEAD") == commits["overfit"]
    assert service.decide("good")["status"] == "QUALIFIED"


def test_budget_identity_immutability_and_parent(
    research: tuple[QualificationService, RunnerService, Path, dict[str, str]],
) -> None:
    service, runner, repo, commits = research
    first = add(service, "h1")
    assert service.add_hypothesis(first)["status"] == "EXISTS"
    with pytest.raises(ExecutionRefused, match="Parent"):
        service.add_hypothesis(
            HypothesisSpec(
                hypothesis_id="h2",
                proposition="P",
                intervention="I",
                falsifier="F",
                parent_id="missing",
            )
        )
    add(service, "h2")
    with pytest.raises(ExecutionRefused, match="budget"):
        add(service, "h3")
    with pytest.raises(ExecutionRefused, match="full immutable"):
        service.register(candidate_id="good", hypothesis_id="h1", commit="HEAD")
    with pytest.raises(ExecutionRefused, match="registered"):
        runner.run(role="dev", approved=True, candidate_id="good")
    service.register(candidate_id="good", hypothesis_id="h1", commit=commits["good"])
    assert (
        service.register(candidate_id="good", hypothesis_id="h1", commit=commits["good"])[
            "candidate_id"
        ]
        == "good"
    )
    with pytest.raises(ExecutionRefused, match="cannot be replaced"):
        service.register(candidate_id="good", hypothesis_id="h2", commit=commits["overfit"])
    with pytest.raises(ExecutionRefused, match="registered"):
        service.register(candidate_id="other", hypothesis_id="h2", commit=commits["good"])
    service.register(candidate_id="overfit", hypothesis_id="h2", commit=commits["overfit"])
    with pytest.raises(ExecutionRefused, match="already registered"):
        service.register(candidate_id="third", hypothesis_id="h1", commit=commits["overfit"])
    (repo / "third.txt").write_text("distinct commit\n")
    git(repo, "add", "third.txt")
    git(repo, "commit", "-qm", "third")
    third = git(repo, "rev-parse", "HEAD")
    with pytest.raises(ExecutionRefused, match="count budget"):
        service.register(candidate_id="third", hypothesis_id="h1", commit=third)


def test_inconclusive_then_append_superseding_decision(
    research: tuple[QualificationService, RunnerService, Path, dict[str, str]],
) -> None:
    service, runner, _, commits = research
    add(service, "h1")
    service.register(candidate_id="good", hypothesis_id="h1", commit=commits["good"])
    for role in ("dev", "qualification"):
        runner.run(role=role, approved=True)
    runner.run(role="dev", approved=True, candidate_id="good")
    first = service.qualify("good")
    assert first["status"] == "INCONCLUSIVE"
    assert service.qualify("good") == first
    with pytest.raises(ExecutionRefused, match="registered"):
        runner.run(role="qualification", approved=True, candidate_id="nonexistent")
    runner.run(role="qualification", approved=True, candidate_id="good")
    second = service.qualify("good")
    assert second["status"] == "QUALIFIED"
    assert second["supersedes_event_sha256"] is not None
    ledger = EventLedger(runner.campaign.root)
    assert [e.event_type for e in ledger.read_events("selection-001")].count(
        "QUALIFICATION_DECIDED"
    ) == 2


def test_replay_refuses_forged_success(
    research: tuple[QualificationService, RunnerService, Path, dict[str, str]],
) -> None:
    service, runner, _, commits = research
    add(service, "h1")
    service.register(candidate_id="good", hypothesis_id="h1", commit=commits["good"])
    runner.run(role="dev", approved=True)
    ledger = EventLedger(runner.campaign.root)
    size = ledger.verify("selection-001")["event_count"]
    assert isinstance(size, int)
    ledger.append_event(
        "selection-001", size, "ATTEMPT_SUCCEEDED", "forged", {"status": "SUCCEEDED"}
    )
    with pytest.raises(RunnerIntegrityError, match="no unique start"):
        service.decide("good")


def test_cli_add_and_register(
    research: tuple[QualificationService, RunnerService, Path, dict[str, str]], tmp_path: Path
) -> None:
    service, runner, _, commits = research
    spec = tmp_path / "hypothesis.json"
    spec.write_text(
        HypothesisSpec(
            hypothesis_id="h1", proposition="P", intervention="I", falsifier="F"
        ).model_dump_json()
    )
    cli = CliRunner()
    c = str(runner.campaign.root)
    assert (
        cli.invoke(app, ["hypothesis", "add", "--campaign", c, "--file", str(spec)]).exit_code == 0
    )
    registration = cli.invoke(
        app,
        [
            "candidate",
            "register",
            "--campaign",
            c,
            "--id",
            "good",
            "--hypothesis",
            "h1",
            "--commit",
            commits["good"],
        ],
    )
    assert registration.exit_code == 0, registration.output
    decision = cli.invoke(app, ["qualify", "--campaign", c, "--candidate", "good"])
    assert decision.exit_code == 0, decision.output
    assert json.loads(decision.output)["data"]["status"] == "INCONCLUSIVE"


def test_forged_qualification_verdict_is_not_reused(
    research: tuple[QualificationService, RunnerService, Path, dict[str, str]],
) -> None:
    service, runner, _, commits = research
    add(service, "h1")
    service.register(candidate_id="good", hypothesis_id="h1", commit=commits["good"])
    ledger = EventLedger(runner.campaign.root)
    seq = ledger.verify("selection-001")["event_count"]
    assert isinstance(seq, int)
    ledger.append_event(
        "selection-001",
        seq,
        "QUALIFICATION_DECIDED",
        "good",
        {"candidate_id": "good", "status": "QUALIFIED", "evidence_events": {}},
    )
    with pytest.raises(RunnerIntegrityError, match="contradicts deterministic replay"):
        service.qualify("good")


def test_hypothesis_forge_admission_is_gated_and_traceable(
    research: tuple[QualificationService, RunnerService, Path, dict[str, str]],
) -> None:
    service, runner, _, commits = research
    design = ResearchDesign(
        schema_version="1.0",
        hypothesis_id="forge-001",
        proposition="A controlled search change should reduce errors",
        intervention="Adjust only the chosen search policy",
        mechanism="Reduce misleading early search branches",
        assumptions=["Pinned baseline and evaluator"],
        confounders=["CPU scheduling and cache state"],
        expected_metric="errors",
        expected_direction="min",
        falsifier="No error reduction on predeclared qualification cases",
        test_plan="Run fixed-seed dev and qualification, compare against baseline",
        max_attempts=2,
    )
    draft = assemble_draft(design)
    with pytest.raises(ExecutionRefused, match="approval"):
        service.admit_forge_draft(
            draft, approved_by_human=False, expected_sha256=draft.draft_sha256
        )
    with pytest.raises(ExecutionRefused, match="digest"):
        service.admit_forge_draft(draft, approved_by_human=True, expected_sha256="0" * 64)
    ledger = EventLedger(runner.campaign.root)
    assert not any(
        event.event_type == "HYPOTHESIS_FORGE_APPROVED"
        for event in ledger.read_events("selection-001")
    )
    result = service.admit_forge_draft(
        draft, approved_by_human=True, expected_sha256=draft.draft_sha256
    )
    assert result["human_approved"] is True
    assert result["evaluation_executed"] is False
    assert (
        service.admit_forge_draft(
            draft, approved_by_human=True, expected_sha256=draft.draft_sha256
        )["status"]
        == "EXISTS"
    )
    events = ledger.read_events("selection-001")
    assert [e.event_type for e in events][-2:] == [
        "HYPOTHESIS_FORGE_APPROVED",
        "HYPOTHESIS_PROPOSED",
    ]
    assert ledger.verify("selection-001")["event_count"] == 3
    assert "forge-001" in service.registry(ledger, "selection-001")[0]
    service.register(candidate_id="good", hypothesis_id="forge-001", commit=commits["good"])
    assert service.decide("good")["status"] == "INCONCLUSIVE"

    mismatched = design.model_copy(
        update={"hypothesis_id": "forge-002", "expected_metric": "other"}
    )
    bad = assemble_draft(mismatched)
    with pytest.raises(ExecutionRefused, match="metric"):
        service.admit_forge_draft(bad, approved_by_human=True, expected_sha256=bad.draft_sha256)

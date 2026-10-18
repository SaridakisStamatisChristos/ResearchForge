"""PR07 real, offline end-to-end evidence and portable skill regression tests."""

from __future__ import annotations

import json
import socket
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from researchforge.application.evidence import EventLedger
from researchforge.application.final import FinalService
from researchforge.application.runner import ExecutionRefused, RunnerIntegrityError, RunnerService
from researchforge.cli import app
from researchforge.demo import run_demo
from researchforge.domain.models import ArtifactRef
from researchforge.portable_skills import (
    PortableSkillError,
    export_skill,
    validate_skill,
)


def _events(root: Path) -> tuple[EventLedger, list[object]]:
    ledger = EventLedger(root / "campaign")
    return ledger, ledger.read_events("toy-optimizer-v1")


def _final_stdout_hash(root: Path) -> str:
    ledger, events = _events(root)
    starts = {}
    for event in events:
        if event.event_type == "ATTEMPT_STARTED":
            starts[event.subject_id] = json.loads(ledger.cas.get(event.payload_ref))
        if event.event_type == "ATTEMPT_SUCCEEDED":
            started = starts[event.subject_id]
            if started.get("role") == "final" and started.get("candidate_id") == "good":
                payload = json.loads(ledger.cas.get(event.payload_ref))
                return str(payload["stdout_ref"]["sha256"])
    raise AssertionError("Missing real, successful candidate final subprocess")


def test_offline_demo_real_subprocesses_sealed_final_and_cli(tmp_path: Path) -> None:
    target = tmp_path / "demonstration"
    runner = CliRunner()
    assert runner.invoke(app, ["demo", "run", "--output", str(target)]).exit_code == 3
    with patch.object(socket, "create_connection", side_effect=AssertionError("network")):
        response = runner.invoke(
            app, ["demo", "run", "--offline", "--output", str(target)]
        )
    assert response.exit_code == 0, response.output
    data = json.loads(response.output)["data"]
    assert data["status"] == "PASS"
    assert data["decision_statuses"] == {"good": "QUALIFIED", "overfit": "REJECTED"}
    assert data["final_status"] == "FINAL_SUPPORTED"
    assert data["negative_evidence"]["reason"] == "QUALIFICATION_THRESHOLD_NOT_MET"
    assert data["qualification_exposure"] == 2
    assert data["final_result"]["measured_improvement"] == 9.5
    assert (target / "report.json").is_file()
    assert (target / "report.md").is_file()
    assert (target / "summary.json").is_file()
    assert runner.invoke(app, ["demo", "run", "--offline", "--output", str(target)]).exit_code == 2

    ledger, events = _events(target)
    assert ledger.verify("toy-optimizer-v1")["event_count"] == 25
    starts = {}
    for event in events:
        if event.event_type == "ATTEMPT_STARTED":
            starts[event.subject_id] = json.loads(ledger.cas.get(event.payload_ref))
    assert len(starts) == 8
    assert len([s for s in starts.values() if s["role"] == "final"]) == 2
    plan_seq = next(e.sequence for e in events if e.event_type == "FINAL_PLAN_FROZEN")
    assert all(
        e.sequence > plan_seq
        for e in events
        if e.event_type == "ATTEMPT_STARTED"
        and starts[e.subject_id]["role"] == "final"
    )
    assert next(e.sequence for e in events if e.event_type == "FINAL_EVALUATION_STARTED") > plan_seq
    assert FinalService(target / "campaign").inspect()["final_status"] == "FINAL_SUPPORTED"
    assert runner.invoke(app, ["verify", "--campaign", str(target / "campaign")]).exit_code == 0
    assert runner.invoke(app, [
        "report", "--campaign", str(target / "campaign"), "--format", "md"
    ]).exit_code == 0
    with pytest.raises(ExecutionRefused, match="one-shot"):
        FinalService(target / "campaign").evaluate(approved=True)
    assert subprocess.check_output([
        "git", "-C", str(target / "toy-repository"), "rev-parse", "HEAD"
    ]).decode().strip() == data["commit_oids"]["overfit"]


def test_repeatable_frozen_data_and_raw_outputs(tmp_path: Path) -> None:
    one = tmp_path / "one"
    two = tmp_path / "two"
    first = run_demo(one)
    second = run_demo(two)
    assert first["decision_statuses"] == second["decision_statuses"]
    assert first["final_status"] == second["final_status"] == "FINAL_SUPPORTED"
    assert first["frozen_evaluator_sha256"] == second["frozen_evaluator_sha256"]
    assert first["frozen_split_sha256"] == second["frozen_split_sha256"]
    assert _final_stdout_hash(one) == _final_stdout_hash(two)
    # Timestamps, unique attempt event IDs and absolute repository paths intentionally differ.
    assert first["ledger_chain_head"] != second["ledger_chain_head"]


def test_blob_tamper_refuses_replay_and_preserves_original_report(tmp_path: Path) -> None:
    root = tmp_path / "corruptible"
    run_demo(root)
    original = (root / "report.json").read_bytes()
    ledger, events = _events(root)
    target = next(e for e in events if e.event_type == "FINAL_EVALUATED")
    ref = ArtifactRef.model_validate_json(target.payload_ref.model_dump_json())
    blob = ledger.cas._path(ref.sha256)
    blob.write_bytes(b"tampered")
    with pytest.raises(RunnerIntegrityError):
        FinalService(root / "campaign").inspect()
    assert CliRunner().invoke(
        app, ["verify", "--campaign", str(root / "campaign")]
    ).exit_code == 6
    assert (root / "report.json").read_bytes() == original


def test_skill_validate_export_and_no_overwrite(tmp_path: Path) -> None:
    bundled = validate_skill()
    assert bundled["valid"] is True
    repo_skill = (
        Path(__file__).resolve().parents[2]
        / "skills" / "researchforge-experiment" / "SKILL.md"
    )
    assert validate_skill(repo_skill) == bundled
    cli = CliRunner()
    assert cli.invoke(app, ["skill", "validate"]).exit_code == 0
    target = tmp_path / "agents" / "skills"
    assert cli.invoke(app, [
        "skill", "export", "--target", str(target)
    ]).exit_code == 0
    exported = target / "researchforge-experiment" / "SKILL.md"
    assert exported.is_file()
    assert exported.read_bytes() == repo_skill.read_bytes()
    assert cli.invoke(app, [
        "skill", "export", "--target", str(target)
    ]).exit_code == 3
    assert exported.read_bytes() == repo_skill.read_bytes()


def test_reject_invalid_skill_or_symlinked_target(tmp_path: Path) -> None:
    invalid = tmp_path / "SKILL.md"
    invalid.write_text("---\nname: wrong\n---\n# bad\n")
    with pytest.raises(PortableSkillError):
        validate_skill(invalid)
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(real, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("Host does not support symlinks")
    with pytest.raises(PortableSkillError, match="symlink"):
        export_skill(alias)
    assert not list(real.iterdir())

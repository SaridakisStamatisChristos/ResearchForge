"""PR14 tests: actual subprocesses, independently rescored evidence and negative results."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from researchforge.domain.serialization import canonical_json, digest_bytes
from researchforge.uplift import (
    UpliftError,
    UpliftPlan,
    UpliftRefused,
    read_uplift_plan,
    run_uplift,
    verify_uplift,
)
from researchforge.uplift_cli import app
from researchforge.uplift_fixture_demo import fixture_plan


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "inputs"
    root.mkdir()
    return fixture_plan(root), tmp_path / "bundle"


def test_real_process_control_full_and_both_ablations(tmp_path: Path) -> None:
    plan, output = _fixture(tmp_path)
    result = run_uplift(plan, output, approved=True)
    assert result["provenance_class"] == "MECHANICAL_FIXTURE"
    assert result["population_uplift_established"] is False
    assert result["task_count"] == 8
    assert len(result["comparisons"]) == 3
    assert result["comparisons"][0]["delta_pass_rate"] == 0.5
    assert result["comparisons"][0]["unknown_pairs"] == 0
    assert all(c["status"] == "DESCRIPTIVE_ONLY" for c in result["comparisons"])
    assert all(s == "PASS" for s in result["arm_statuses"]["full"].values())
    assert result == verify_uplift(output)
    assert "population uplift established: **NO**" in (output / "report.md").read_text()
    assert (output / "trials" / "parity1" / "control" / "context").is_dir()
    assert not list((output / "trials" / "parity1" / "control" / "context").iterdir())
    assert len(list((output / "trials" / "parity1" / "full" / "context").iterdir())) == 2
    with pytest.raises(FileExistsError):
        run_uplift(plan, output, approved=True)


def test_score_tampering_and_raw_output_tampering_detected(tmp_path: Path) -> None:
    plan, output = _fixture(tmp_path)
    run_uplift(plan, output, approved=True)
    target = output / "trials" / "parity1" / "full" / "captured_answer.json"
    target.write_text('{"answer":"ODD"}\n', encoding="utf-8")
    with pytest.raises(UpliftError):
        verify_uplift(output)

    # Restore captured bytes, falsify the recorded score AND rehash the receipt:
    # independent replay must still reject the fabricated decision.
    target.write_text('{"answer": "ODD"}\n', encoding="utf-8")
    receipt = json.loads((output / "receipt.json").read_text())
    row = next(r for r in receipt["rows"] if r["task_id"] == "parity1" and r["arm"] == "full")
    target.write_text('{"answer": "ODD"}\n', encoding="utf-8")
    row["status"] = "FAIL"
    row["answer_sha256"] = digest_bytes(target.read_bytes())
    # Raw bytes do match the original answer for odd parity; the row score does not.
    row["status"] = "PASS"
    row["reason"] = "FABRICATED"
    changed = canonical_json(receipt)
    (output / "receipt.json").write_bytes(changed)
    (output / "receipt.sha256").write_text(digest_bytes(changed) + "\n")
    with pytest.raises(UpliftError):
        verify_uplift(output)


def test_no_execution_without_approval_and_no_unpinned_script(tmp_path: Path) -> None:
    plan, output = _fixture(tmp_path)
    with pytest.raises(UpliftRefused):
        run_uplift(plan, output, approved=False)
    assert not output.exists()
    payload = json.loads(plan.read_text())
    payload["arms"][0]["pinned_files"] = {"/tmp/absent": "0" * 64}
    plan.write_bytes(canonical_json(payload))
    with pytest.raises(UpliftRefused):
        run_uplift(plan, output, approved=True)
    assert not output.exists()


def test_invalid_ablation_missing_task_duplicate_and_rejected_fields(tmp_path: Path) -> None:
    plan, _ = _fixture(tmp_path)
    payload = json.loads(plan.read_text())
    payload["arms"][2]["components"] = []
    with pytest.raises(ValidationError):
        UpliftPlan.model_validate(payload)
    payload = json.loads(plan.read_text())
    payload["tasks"][1]["task_id"] = payload["tasks"][0]["task_id"]
    with pytest.raises(ValidationError):
        UpliftPlan.model_validate(payload)
    payload = json.loads(plan.read_text())
    payload["irrelevant_injected_field"] = "ignored?"
    with pytest.raises(ValidationError):
        UpliftPlan.model_validate(payload)
    payload = json.loads(plan.read_text())
    payload["seed"] = float("nan")
    plan.write_text(json.dumps(payload))
    with pytest.raises(UpliftError):
        read_uplift_plan(plan)


def test_nonpositive_results_are_not_hidden(tmp_path: Path) -> None:
    plan, bundle = _fixture(tmp_path)
    payload = json.loads(plan.read_text())
    # All are tasks the deliberately weak mechanical control can answer.
    for task in payload["tasks"]:
        if task["family"] == "parity":
            task["prompt"] = "PARITY 2"
            task["expected_answer"] = "EVEN"
        else:
            task["prompt"] = "SIGN 2"
            task["expected_answer"] = "POSITIVE"
    plan.write_bytes(canonical_json(payload))
    report = run_uplift(plan, bundle, approved=True)
    assert len(report["negative_or_null_findings"]) == 3
    assert all(c["delta_pass_rate"] == 0 for c in report["comparisons"])
    assert report["comparisons"][0]["uncertainty"]["interval_lower"] == 0


def test_nonresponding_agent_is_inconclusive_not_zero_score(tmp_path: Path) -> None:
    plan, bundle = _fixture(tmp_path)
    stub = tmp_path / "no_answer.py"
    stub.write_text("pass\n", encoding="utf-8")
    payload = json.loads(plan.read_text())
    for arm in payload["arms"]:
        arm["argv"][1] = str(stub)
        arm["pinned_files"] = {str(stub): digest_bytes(stub.read_bytes())}
    plan.write_bytes(canonical_json(payload))
    report = run_uplift(plan, bundle, approved=True)
    assert report["comparisons"][0]["status"] == "INCONCLUSIVE"
    assert report["comparisons"][0]["delta_pass_rate"] is None
    assert all(c["uncertainty"] is None for c in report["comparisons"])
    assert report["population_uplift_established"] is False


def test_cli_registration_and_approval_gate(tmp_path: Path) -> None:
    plan, output = _fixture(tmp_path)
    runner = CliRunner()
    assert runner.invoke(app, ["--version"]).exit_code == 0
    result = runner.invoke(app, ["uplift", "run", "--plan", str(plan), "--output", str(output)])
    assert result.exit_code == 3
    assert not output.exists()

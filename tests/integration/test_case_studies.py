"""PR13 real local subprocess fixtures, adversarial SAT/graph oracles and raw replay."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from researchforge.case_demo import run_case_demo
from researchforge.case_oracles import (
    CaseInputError,
    parse_dimacs,
    read_graph,
    verify_graph,
    verify_sat,
)
from researchforge.case_studies import (
    CaseBundleError,
    CaseExecutionRefused,
    CasePlan,
    read_plan,
    run_cases,
    verify_bundle,
)
from researchforge.cli import app
from researchforge.domain.serialization import canonical_json, digest_bytes


def test_sat_oracle_verifies_witness_and_rejects_false_unsat() -> None:
    satisfiable = b"c tiny\np cnf 2 2\n1 2 0\n-1 2 0\n"
    unsatisfiable = b"p cnf 1 2\n1 0\n-1 0\n"
    assert verify_sat(satisfiable, b"s SATISFIABLE\nv 2 0\n", 10)[0] == "VERIFIED_SAT"
    assert verify_sat(satisfiable, b"s SATISFIABLE\nv -1 -2 0\n", 10)[0] == "WRONG_ANSWER"
    assert verify_sat(satisfiable, b"s UNSATISFIABLE\n", 20)[0] == "WRONG_ANSWER"
    assert verify_sat(unsatisfiable, b"s UNSATISFIABLE\n", 20)[0] == "VERIFIED_UNSAT"
    assert verify_sat(unsatisfiable, b"s SATISFIABLE\nv 1 0\n", 10)[0] == "WRONG_ANSWER"
    assert verify_sat(satisfiable, b"s SATISFIABLE\ns UNSATISFIABLE\n", 10)[0] == "UNKNOWN"
    assert verify_sat(satisfiable, b"s SATISFIABLE\nv 2 0\n", 20)[0] == "UNKNOWN"
    assert verify_sat(satisfiable, b"s SATISFIABLE\nv 2 -2 0\n", 10)[0] == "WRONG_ANSWER"
    big = b"p cnf 17 1\n1 0\n"
    assert verify_sat(big, b"s UNSATISFIABLE\n", 20)[0] == "UNVERIFIED_UNSAT"
    with pytest.raises(CaseInputError):
        parse_dimacs(b"p cnf 1 1\n2 0\n")
    with pytest.raises(CaseInputError):
        parse_dimacs(b"p cnf 1 2\n1 0\n")


def test_graph_oracle_is_not_claimed_from_invalid_or_incomplete_results() -> None:
    instance = canonical_json(
        {
            "nodes": ["s", "a", "t", "isolated"],
            "source": "s",
            "edges": [["s", "a", 2], ["a", "t", 1], ["s", "t", 5]],
        }
    )
    expected = canonical_json({"distances": {"s": 0, "a": 2, "t": 3, "isolated": None}})
    assert verify_graph(instance, expected, 0)[0] == "VERIFIED_GRAPH"
    assert verify_graph(instance, expected.replace(b'"t":3', b'"t":4'), 0)[0] == "WRONG_ANSWER"
    assert verify_graph(instance, expected, 1)[0] == "UNKNOWN"
    assert verify_graph(instance, b'{"distances":{"s":0}}', 0)[0] == "UNKNOWN"
    with pytest.raises(CaseInputError):
        read_graph(b'{"nodes":["s"],"source":"s","edges":[["s","s",-1]]}')
    with pytest.raises(CaseInputError):
        read_graph(b'{"nodes":["s"],"source":"s","edges":[],"edges":[]}')


def test_actual_subprocess_raw_logs_two_independent_domains(tmp_path: Path) -> None:
    result = run_case_demo(tmp_path / "fixture")
    assert result["kind"] == "OFFLINE_TOY_REFERENCE_FIXTURES"
    for kind in ("sat", "shortest_path"):
        root = tmp_path / "fixture" / kind
        plan = read_plan(root / "plan.json")
        assert plan.kind == kind
        replay = verify_bundle(root / "raw-bundle")
        assert replay["case_count"] == 3
        assert replay["certified_solved"] == {"toy-reference": 3, "toy-defective": 2}
        assert replay["paired_discordance"] == {"left_only": 1, "right_only": 0}
        assert replay["paired_solved_bootstrap"]["observation_count"] == 3
        assert replay["bundle_sha256"] == result["domains"][kind]["raw_bundle_sha256"]
        assert any(r["right"]["status"] == "WRONG_ANSWER" for r in replay["cases"])
        assert all(r["left"]["status"].startswith("VERIFIED") for r in replay["cases"])
        assert replay["source"] == "OFFLINE_REPLAY_OF_TRUSTED_LOCAL_RECEIPT"
        assert "not independently clock-verified" in replay["limitations"][0]
        assert len(list((root / "raw-bundle" / "logs").rglob("*.stdout"))) == 6
        cli = CliRunner().invoke(app, ["cases", "verify", "--bundle", str(root / "raw-bundle")])
        assert cli.exit_code == 0, cli.output
        assert json.loads(cli.output)["bundle_sha256"] == replay["bundle_sha256"]


def test_approval_and_no_clobber_are_real_guards(tmp_path: Path) -> None:
    run_case_demo(tmp_path / "fixture")
    plan = tmp_path / "fixture" / "sat" / "plan.json"
    proposed_output = tmp_path / "second"
    with pytest.raises(CaseExecutionRefused):
        run_cases(plan, proposed_output, approved=False)
    assert not proposed_output.exists()
    success = CliRunner().invoke(
        app,
        [
            "cases",
            "run",
            "--plan",
            str(plan),
            "--output",
            str(proposed_output),
            "--approve-trusted-local",
        ],
    )
    assert success.exit_code == 0, success.output
    again = CliRunner().invoke(
        app,
        [
            "cases",
            "run",
            "--plan",
            str(plan),
            "--output",
            str(proposed_output),
            "--approve-trusted-local",
        ],
    )
    assert again.exit_code == 6
    assert json.loads((proposed_output / "bundle.json").read_text())["rows"]


def test_raw_log_replay_fails_closed_on_mutated_provenance(tmp_path: Path) -> None:
    run_case_demo(tmp_path / "fixture")
    folder = tmp_path / "fixture" / "sat" / "raw-bundle"
    bundle = folder / "bundle.json"
    first = json.loads(bundle.read_bytes())
    first["rows"][0]["correctness"] = "VERIFIED_UNSAT"
    bundle.write_bytes(canonical_json(first))
    (folder / "bundle.sha256").write_text(digest_bytes(bundle.read_bytes()) + "\n")
    with pytest.raises(CaseBundleError, match="oracle"):
        verify_bundle(folder)


def test_missing_binary_or_corrupt_logs_refuse_execution_or_replay(tmp_path: Path) -> None:
    run_case_demo(tmp_path / "fixture")
    root = tmp_path / "fixture" / "shortest_path"
    folder = root / "raw-bundle"
    (folder / "logs" / "path-one" / "toy-reference.stdout").write_bytes(b'{"distances":{}}')
    with pytest.raises(CaseBundleError, match="digest"):
        verify_bundle(folder)
    plan = json.loads((root / "plan.json").read_text())
    plan["left"]["binary_sha256"] = "0" * 64
    revised = root / "bad-plan.json"
    revised.write_bytes(canonical_json(plan))
    with pytest.raises(CaseExecutionRefused, match="SHA-256"):
        run_cases(revised, tmp_path / "never", approved=True)
    assert not (tmp_path / "never").exists()


def test_strict_schema_rejects_unpaired_or_path_escape(tmp_path: Path) -> None:
    run_case_demo(tmp_path / "fixture")
    plan = json.loads((tmp_path / "fixture" / "sat" / "plan.json").read_text())
    plan["cases"].append(plan["cases"][0])
    with pytest.raises(ValidationError, match="Duplicate case"):
        CasePlan.model_validate(plan)
    plan["cases"].pop()
    plan["cases"][0]["input_path"] = "../other.cnf"
    with pytest.raises(ValidationError):
        CasePlan.model_validate(plan)
    plan["cases"][0]["input_path"] = "cases/sat-one.cnf"
    plan["left"]["argv"][0] = "python"
    with pytest.raises(ValidationError):
        CasePlan.model_validate(plan)
    bad = tmp_path / "duplicate.json"
    bad.write_text('{"schema_version":"1.0","schema_version":"1.0"}')
    with pytest.raises(CaseBundleError):
        read_plan(bad)


def test_replay_rejects_argv_switch_after_rehashed_bundle(tmp_path: Path) -> None:
    run_case_demo(tmp_path / "fixture")
    folder = tmp_path / "fixture" / "sat" / "raw-bundle"
    bundle_file = folder / "bundle.json"
    original = json.loads(bundle_file.read_bytes())
    original["rows"][0]["argv"] = ["/bin/false", "switch"]
    changed = canonical_json(original)
    bundle_file.write_bytes(changed)
    (folder / "bundle.sha256").write_text(digest_bytes(changed) + "\n")
    with pytest.raises(CaseBundleError, match="argv"):
        verify_bundle(folder)


def test_pinned_wrapper_source_drift_refuses_execution(tmp_path: Path) -> None:
    run_case_demo(tmp_path / "fixture")
    source = tmp_path / "fixture" / "sat"
    wrapper = source / "toy_program.py"
    wrapper.write_text(wrapper.read_text() + "\n# changed\n")
    with pytest.raises(CaseExecutionRefused, match="auxiliary"):
        run_cases(source / "plan.json", tmp_path / "refused", approved=True)
    assert not (tmp_path / "refused").exists()


def test_schema_is_publicly_exposed() -> None:
    result = CliRunner().invoke(app, ["contracts", "schema", "--name", "case-plan"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["title"] == "CasePlan"

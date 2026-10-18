"""PR11 proposal parsing, consent boundaries and model-response contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from researchforge.cli import app
from researchforge.hypothesis_forge import (
    ForgeInputError,
    HypothesisDraft,
    ResearchDesign,
    assemble_draft,
    generate_model_draft,
    read_design,
    read_draft,
    save_draft,
)


def example_design() -> ResearchDesign:
    return ResearchDesign(
        schema_version="1.0",
        hypothesis_id="hf-001",
        proposition="A deterministic policy reduces evaluator errors",
        intervention="Replace only the phase-choice rule",
        mechanism="Earlier correct assignments reduce wasted search",
        assumptions=["Same baseline and evaluator version"],
        confounders=["Hardware contention during timed experiments"],
        expected_metric="errors",
        expected_direction="min",
        falsifier="No improvement on preregistered qualification set",
        test_plan="Use fixed seeds on dev, then qualification under frozen limits",
        max_attempts=2,
    )


def test_human_draft_roundtrip_and_no_clobber(tmp_path: Path) -> None:
    draft = assemble_draft(example_design())
    assert draft.status == "HUMAN_DRAFT"
    assert draft.provenance is None
    path = tmp_path / "draft.json"
    save_draft(path, draft)
    assert read_draft(path) == draft
    with pytest.raises(FileExistsError):
        save_draft(path, draft)
    invalid = json.loads(path.read_text())
    invalid["design"]["falsifier"] = "a different falsifier"
    path.write_text(json.dumps(invalid))
    with pytest.raises(ValidationError, match="digest mismatch"):
        read_draft(path)


def test_strict_falsifiability_and_json_parser(tmp_path: Path) -> None:
    d = example_design().model_dump()
    d["assumptions"] = [""]
    with pytest.raises(ValidationError):
        ResearchDesign.model_validate(d)
    d = example_design().model_dump()
    d["max_attempts"] = 0
    with pytest.raises(ValidationError):
        ResearchDesign.model_validate(d)
    source = tmp_path / "input.json"
    source.write_text('{"schema_version":"1.0","schema_version":"1.0"}')
    with pytest.raises(ForgeInputError, match="Duplicate"):
        read_design(source)
    source.write_text(example_design().model_dump_json()[:-1] + ',"approval":"yes"}')
    with pytest.raises(ValidationError):
        read_design(source)


def test_model_generation_is_opt_in_unverified_and_bounded() -> None:
    design = example_design().model_dump(mode="json")
    response = json.dumps({"choices": [{"message": {"content": json.dumps(design)}}]}).encode()
    calls = []

    def fake_transport(body: bytes, key: str) -> bytes:
        calls.append((json.loads(body), key))
        return response

    with pytest.raises(ForgeInputError, match="explicit"):
        generate_model_draft(
            "Test phase control", model="unit-test-model",
            allow_network=False, api_key="test", transport=fake_transport
        )
    assert not calls
    draft = generate_model_draft(
        "Test phase control", model="unit-test-model",
        allow_network=True, api_key="test", transport=fake_transport
    )
    assert draft.status == "AI_PROPOSED_UNVERIFIED"
    assert draft.provenance is not None
    assert draft.provenance.provider == "openai"
    assert calls[0][0]["model"] == "unit-test-model"
    assert HypothesisDraft.model_validate_json(draft.model_dump_json()) == draft
    with pytest.raises(ForgeInputError, match="exceeds"):
        generate_model_draft(
            "Research", model="unit-test-model", allow_network=True,
            api_key="test", transport=lambda _b, _k: b"x" * 65537
        )
    with pytest.raises(ValidationError):
        generate_model_draft(
            "Research", model="unit-test-model", allow_network=True,
            api_key="test", transport=lambda _b, _k: (
                json.dumps({"choices": [{"message": {"content": '{"status":"QUALIFIED"}'}}]})
                .encode()
            )
        )


def test_cli_explicit_review_boundary(tmp_path: Path) -> None:
    runner = CliRunner()
    input_file = tmp_path / "design.json"
    input_file.write_text(example_design().model_dump_json())
    out = tmp_path / "draft.json"
    res = runner.invoke(
        app, ["hypothesis", "draft", "--file", str(input_file), "--output", str(out)]
    )
    assert res.exit_code == 0, res.output
    digest = json.loads(res.output)["data"]["draft_sha256"]
    assert len(digest) == 64
    assert read_draft(out).draft_sha256 == digest
    res = runner.invoke(
        app, ["hypothesis", "approve", "--campaign", str(tmp_path),
              "--file", str(out), "--expect-sha256", digest]
    )
    assert res.exit_code == 3
    res = runner.invoke(
        app, ["hypothesis", "generate", "--brief", str(input_file),
              "--output", str(tmp_path / "model.json"), "--model", "mock"]
    )
    assert res.exit_code == 3
    assert not (tmp_path / "model.json").exists()
    res = runner.invoke(app, ["contracts", "schema", "--name", "hypothesis-design"])
    assert res.exit_code == 0
    assert "test_plan" in res.output

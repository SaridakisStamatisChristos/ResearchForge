"""RF-ARCH-001 contract checks only; no simulated evaluator execution."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from researchforge.domain.envelope import ApiError, ResponseEnvelope
from researchforge.domain.models import CampaignManifest, EvaluationResult, EvidenceEvent


@pytest.fixture
def manifest_data() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "campaign_id": "toy-001",
        "objective": "Minimize errors",
        "base": {"repository": "file:///trusted/example", "commit": "a" * 40},
        "metric": {"name": "error_count", "direction": "min", "unit": "errors"},
        "evaluation": {
            "argv": ["python", "evaluator.py", "--split", "{split_file}"],
            "cwd": "{evaluator_dir}",
            "timeout_seconds": 10,
            "max_output_bytes": 1000,
            "result_schema": "evaluator-result-v1",
        },
        "splits": {
            "dev": "splits/dev.json",
            "qualification": "splits/qualification.json",
            "final": "splits/final.json",
        },
        "budget": {"max_hypotheses": 2, "max_attempts": 5, "max_wall_seconds": 200},
        "qualification_policy": {
            "name": "strict-improvement-v1",
            "baseline_required": True,
            "minimum_absolute_improvement": 1,
            "correctness_checks": ["result-schema", "sample-count"],
            "allow_equal": False,
        },
        "seed": 1701,
        "execution_trust": "local_trusted_only",
    }


def test_manifest_frozen_and_roundtrip(manifest_data: dict[str, object]) -> None:
    obj = CampaignManifest.model_validate(manifest_data)
    assert CampaignManifest.model_validate_json(obj.model_dump_json()) == obj
    with pytest.raises(ValidationError):
        obj.campaign_id = "changed"


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(schema_version="2.0"),
    lambda d: d.update(execution_trust="remote_unsandboxed"),
    lambda d: d.update(unknown="extra"),
    lambda d: d["splits"].update(final="splits/dev.json"),  # type: ignore[union-attr]
    lambda d: d["budget"].update(max_attempts=0),  # type: ignore[union-attr]
    lambda d: d["evaluation"].update(argv=["python", ""]),  # type: ignore[union-attr]
    lambda d: d["qualification_policy"].update(  # type: ignore[union-attr]
        minimum_absolute_improvement=float("nan")
    ),
])
def test_manifest_rejects_invalid(manifest_data: dict[str, object], mutation: object) -> None:
    assert callable(mutation)
    mutation(manifest_data)
    with pytest.raises(ValidationError):
        CampaignManifest.model_validate(manifest_data)


def test_result_rejects_forged_shape_and_nonfinite() -> None:
    payload = {
        "schema_version": "1.0", "status": "complete", "metric": "error_count",
        "direction": "min", "value": 3, "unit": "errors", "sample_count": 10,
        "samples_sha256": "c" * 64,
        "correctness": {"passed": True, "validator_id": "toy-v1"},
        "details": {"failures": 3},
    }
    assert EvaluationResult.model_validate(payload).value == 3
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValidationError):
            EvaluationResult.model_validate({**payload, "value": value})
    with pytest.raises(ValidationError):
        EvaluationResult.model_validate({**payload, "sample_count": 0})
    with pytest.raises(ValidationError):
        EvaluationResult.model_validate({**payload, "details": {"x": float("nan")}})
    with pytest.raises(ValidationError):
        EvaluationResult.model_validate({**payload, "status": "partial"})


def test_event_utc_digest_identity() -> None:
    payload = {
        "event_schema": "1.0", "event_id": str(uuid4()), "campaign_id": "toy-001",
        "sequence": 1, "event_type": "CREATED", "occurred_at_utc": "2026-10-18T10:00:00Z",
        "actor": {"kind": "local_process", "id": "rf"}, "subject_id": "toy-001",
        "payload_ref": {"sha256": "b" * 64, "size_bytes": 22},
        "previous_event_sha256": None, "event_sha256": "c" * 64,
    }
    assert EvidenceEvent.model_validate_json(json.dumps(payload)).sequence == 1
    with pytest.raises(ValidationError):
        EvidenceEvent.model_validate({**payload, "occurred_at_utc": datetime.now()})
    with pytest.raises(ValidationError):
        EvidenceEvent.model_validate({**payload, "event_sha256": "not-a-digest"})
    assert datetime.now(timezone.utc).utcoffset().total_seconds() == 0


def test_error_envelope_serialization() -> None:
    envelope = ResponseEnvelope(
        status="INVALID_INPUT", errors=[ApiError(code="BAD_INPUT", message="Invalid manifest")]
    )
    data = json.loads(envelope.model_dump_json())
    assert data["schema_version"] == "1.0"
    assert data["errors"][0]["code"] == "BAD_INPUT"
    assert isinstance(data["operation_id"], str)

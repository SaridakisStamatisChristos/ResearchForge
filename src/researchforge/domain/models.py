"""Frozen v1 contracts; no storage, process or network side effects.

Schemas here validate untrusted input; they do not assert that a reported result
actually came from an executed evaluator. That proof belongs to later PRs.
"""

from __future__ import annotations

from datetime import datetime
from math import isfinite
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
CommitOID = Annotated[str, Field(pattern=r"^(?:[a-f0-9]{40}|[a-f0-9]{64})$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


Direction = Literal["min", "max"]
SplitRole = Literal["dev", "qualification", "final"]


class RepositoryBase(StrictModel):
    repository: str = Field(min_length=1)
    commit: str = Field(min_length=1)


class MetricSpec(StrictModel):
    name: str = Field(min_length=1)
    direction: Direction
    unit: str = Field(min_length=1)


class EvaluatorSpec(StrictModel):
    argv: list[str] = Field(min_length=1)
    cwd: str = Field(min_length=1)
    timeout_seconds: int = Field(gt=0)
    max_output_bytes: int = Field(gt=0)
    result_schema: Literal["evaluator-result-v1"]

    @field_validator("argv")
    @classmethod
    def nonempty_argv(cls, value: list[str]) -> list[str]:
        if any(not item or "\x00" in item for item in value):
            raise ValueError("argv entries must be nonempty and contain no NUL")
        return value


class Budget(StrictModel):
    max_hypotheses: int = Field(gt=0)
    max_attempts: int = Field(gt=0)
    max_wall_seconds: int = Field(gt=0)


class QualificationPolicy(StrictModel):
    name: Literal["strict-improvement-v1"]
    baseline_required: bool
    minimum_absolute_improvement: float = Field(ge=0, allow_inf_nan=False)
    correctness_checks: list[str] = Field(min_length=1)
    allow_equal: bool


class Splits(StrictModel):
    dev: str = Field(min_length=1)
    qualification: str = Field(min_length=1)
    final: str = Field(min_length=1)

    @model_validator(mode="after")
    def unique_paths(self) -> Splits:
        if len({self.dev, self.qualification, self.final}) != 3:
            raise ValueError("split roles must point to distinct paths")
        return self


class CampaignManifest(StrictModel):
    schema_version: Literal["1.0"]
    campaign_id: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    base: RepositoryBase
    metric: MetricSpec
    evaluation: EvaluatorSpec
    splits: Splits
    budget: Budget
    qualification_policy: QualificationPolicy
    seed: int = Field(ge=0)
    execution_trust: Literal["local_trusted_only"]


class ArtifactRef(StrictModel):
    sha256: Digest
    size_bytes: int = Field(ge=0)


class EventActor(StrictModel):
    kind: Literal["local_process", "human", "agent"]
    id: str = Field(min_length=1)


class EvidenceEvent(StrictModel):
    event_schema: Literal["1.0"]
    event_id: UUID
    campaign_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    event_type: str = Field(min_length=1)
    occurred_at_utc: datetime
    actor: EventActor
    subject_id: str = Field(min_length=1)
    payload_ref: ArtifactRef
    previous_event_sha256: Digest | None
    event_sha256: Digest

    @field_validator("occurred_at_utc")
    @classmethod
    def utc_required(cls, value: datetime) -> datetime:
        offset = value.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError("occurred_at_utc must have a UTC offset")
        return value


class Correctness(StrictModel):
    passed: bool
    validator_id: str = Field(min_length=1)


class EvaluationResult(StrictModel):
    schema_version: Literal["1.0"]
    status: Literal["complete"]
    metric: str = Field(min_length=1)
    direction: Direction
    value: float
    unit: str = Field(min_length=1)
    sample_count: int = Field(gt=0)
    samples_sha256: Digest
    correctness: Correctness
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @field_validator("value")
    @classmethod
    def finite_value(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("value must be finite")
        return value

    @model_validator(mode="after")
    def finite_details(self) -> EvaluationResult:
        if any(isinstance(v, float) and not isfinite(v) for v in self.details.values()):
            raise ValueError("details numbers must be finite")
        return self

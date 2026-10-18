"""Stable machine-readable CLI response format."""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import Field

from researchforge.domain.models import StrictModel


class ApiError(StrictModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class ResponseEnvelope(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    operation_id: str = Field(default_factory=lambda: str(uuid4()))
    status: Literal["OK", "INVALID_INPUT", "REFUSED", "FAILED", "UNKNOWN", "CORRUPT"]
    data: dict[str, Any] = Field(default_factory=dict)
    errors: list[ApiError] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

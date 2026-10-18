"""PR11 HypothesisForge: bounded, non-executing, falsifiable research drafts.

Drafts are untrusted suggestions. Only an explicit human approval may admit one
to the append-only campaign ledger. No evaluator or final split is accessed.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from researchforge.domain.models import Digest, Direction, StrictModel
from researchforge.domain.serialization import canonical_json


class ForgeInputError(ValueError):
    """Invalid or untrusted proposed research design."""


class ResearchDesign(StrictModel):
    schema_version: Literal["1.0"]
    hypothesis_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    parent_id: str | None = None
    proposition: str = Field(min_length=12, max_length=4000)
    intervention: str = Field(min_length=8, max_length=4000)
    mechanism: str = Field(min_length=8, max_length=4000)
    assumptions: list[str] = Field(min_length=1, max_length=20)
    confounders: list[str] = Field(min_length=1, max_length=20)
    expected_metric: str = Field(min_length=1, max_length=120)
    expected_direction: Direction
    falsifier: str = Field(min_length=8, max_length=4000)
    test_plan: str = Field(min_length=12, max_length=6000)
    max_attempts: int = Field(gt=0, le=10000)

    @model_validator(mode="after")
    def reject_empty_items(self) -> ResearchDesign:
        if any(not x.strip() or len(x) > 1000 for x in self.assumptions + self.confounders):
            raise ValueError("Assumptions and confounders must be nonblank and bounded")
        return self


class ModelProvenance(StrictModel):
    provider: Literal["openai"]
    model: str = Field(min_length=1, max_length=120)
    prompt_sha256: Digest
    response_sha256: Digest


class HypothesisDraft(StrictModel):
    schema_version: Literal["1.0"]
    status: Literal["HUMAN_DRAFT", "AI_PROPOSED_UNVERIFIED"]
    design: ResearchDesign
    provenance: ModelProvenance | None = None
    draft_sha256: Digest

    @model_validator(mode="after")
    def verify_identity(self) -> HypothesisDraft:
        if (self.status == "AI_PROPOSED_UNVERIFIED") != (self.provenance is not None):
            raise ValueError("Model provenance must match unverified draft status")
        if self.draft_sha256 != _digest(_draft_body(self.status, self.design, self.provenance)):
            raise ValueError("Hypothesis draft digest mismatch")
        return self


def _digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _draft_body(
    status: str, design: ResearchDesign, provenance: ModelProvenance | None
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "status": status,
        "design": design.model_dump(mode="json"),
        "provenance": provenance.model_dump(mode="json") if provenance else None,
    }


def assemble_draft(
    design: ResearchDesign, provenance: ModelProvenance | None = None
) -> HypothesisDraft:
    status = "AI_PROPOSED_UNVERIFIED" if provenance else "HUMAN_DRAFT"
    return HypothesisDraft(
        **_draft_body(status, design, provenance),
        draft_sha256=_digest(_draft_body(status, design, provenance)),
    )


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ForgeInputError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ForgeInputError(f"Non-finite JSON constant: {value}")


def _strict_json(data: bytes) -> Any:
    if len(data) > 65536:
        raise ForgeInputError("Draft input exceeds 64 KiB")
    return json.loads(data, object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)


def read_design(path: Path) -> ResearchDesign:
    return ResearchDesign.model_validate(_strict_json(path.read_bytes()))


def read_draft(path: Path) -> HypothesisDraft:
    return HypothesisDraft.model_validate(_strict_json(path.read_bytes()))


def save_draft(path: Path, draft: HypothesisDraft) -> None:
    """Create only: a reviewed draft is not overwritten by a later generation."""
    with path.open("x", encoding="utf-8") as handle:
        handle.write(draft.model_dump_json(indent=2) + "\n")


# Injectable network transport enables strictly offline contract tests.
Transport = Callable[[bytes, str], bytes]


def _post_openai(body: bytes, api_key: str) -> bytes:
    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            content = bytes(response.read(65537))
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ForgeInputError("Model provider request failed") from exc
    if len(content) > 65536:
        raise ForgeInputError("Model response exceeds 64 KiB")
    return content


def generate_model_draft(
    brief: str,
    *,
    model: str,
    allow_network: bool,
    api_key: str | None = None,
    transport: Transport | None = None,
) -> HypothesisDraft:
    """Explicit opt-in provider call; responses are never approved or executed."""
    if not allow_network:
        raise ForgeInputError("Model use requires explicit --allow-model-network")
    if not brief.strip() or len(brief) > 8000:
        raise ForgeInputError("Research brief must be nonempty and at most 8000 chars")
    if not model.strip() or len(model) > 120:
        raise ForgeInputError("Invalid model name")
    key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise ForgeInputError("OPENAI_API_KEY is required only for model generation")
    prompt = (
        "Return exactly one JSON object matching this schema, no Markdown: "
        + json.dumps(ResearchDesign.model_json_schema(), sort_keys=True)
        + "\nDo not claim the experiment was executed, or access any data splits.\n"
        + brief
    )
    request = canonical_json(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        }
    )
    raw = (transport or _post_openai)(request, key)
    if len(raw) > 65536:
        raise ForgeInputError("Model response exceeds 64 KiB")
    try:
        response = _strict_json(raw)
        message = response["choices"][0]["message"]["content"]
        if not isinstance(message, str):
            raise ForgeInputError("Model content must be JSON text")
        design = ResearchDesign.model_validate(_strict_json(message.encode("utf-8")))
    except (KeyError, IndexError, TypeError) as exc:
        raise ForgeInputError("Malformed model response envelope") from exc
    provenance = ModelProvenance(
        provider="openai",
        model=model,
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        response_sha256=hashlib.sha256(raw).hexdigest(),
    )
    return assemble_draft(design, provenance)

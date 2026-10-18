"""Artifact storage contract independent of filesystem implementation."""

from __future__ import annotations

from typing import Protocol

from researchforge.domain.models import ArtifactRef


class ArtifactStore(Protocol):
    def put(self, content: bytes) -> ArtifactRef: ...

    def get(self, ref: ArtifactRef) -> bytes: ...

    def verify(self, ref: ArtifactRef) -> bool: ...

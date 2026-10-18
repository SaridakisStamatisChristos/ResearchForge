"""Immutable, content-addressed literature retrieval snapshots and offline reports."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from researchforge.domain.serialization import canonical_json, digest_bytes
from researchforge.literature import (
    LiteratureRecord,
    RetrievalBatch,
    group_by_doi,
)

_ID = re.compile(r"^[a-f0-9]{64}$")
MAX_ARCHIVE = 15_000_000


class LiteratureArchiveError(ValueError):
    """The named immutable archive is unavailable or fails offline validation."""


def _validate_items(batches: list[RetrievalBatch]) -> None:
    for batch in batches:
        if batch.retrieved_total != len(batch.items):
            raise LiteratureArchiveError("Retrieval count contradicts stored items")
        for item in batch.items:
            if digest_bytes(canonical_json(item.raw)) != item.raw_sha256:
                raise LiteratureArchiveError("Stored provider record snapshot hash mismatch")


def snapshot(batches: list[RetrievalBatch]) -> dict[str, Any]:
    _validate_items(batches)
    groups = group_by_doi(batches)
    return {
        "schema_version": "1.0",
        "batches": [batch.model_dump(mode="json") for batch in batches],
        "canonical_groups": {
            key: [
                {"provider": item.provider, "provider_id": item.provider_id,
                 "record_sha256": item.raw_sha256}
                for item in rows
            ]
            for key, rows in sorted(groups.items())
        },
        "interpretation": (
            "Source-specific observations; shared DOI is the only automatic grouping key. "
            "Neither absence from one provider nor title similarity proves global discoverability."
        ),
    }


def store_snapshot(folder: Path, batches: list[RetrievalBatch]) -> str:
    """Write once by digest; an existing path is verified, never overwritten."""
    body = canonical_json(snapshot(batches))
    if len(body) > MAX_ARCHIVE:
        raise LiteratureArchiveError("Retrieval archive too large")
    key = digest_bytes(body)
    if folder.is_symlink():
        raise LiteratureArchiveError("Refusing symlinked archive directory")
    folder.mkdir(parents=True, exist_ok=True)
    file = folder / f"{key}.json"
    try:
        with file.open("xb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        if file.is_symlink() or file.read_bytes() != body:
            raise LiteratureArchiveError("Existing literature archive conflicts with digest")
    return key


def read_snapshot(folder: Path, search_id: str) -> dict[str, Any]:
    """Replay only local snapshots; verify canonical payload, identity and item digests."""
    if not _ID.fullmatch(search_id) or folder.is_symlink():
        raise LiteratureArchiveError("Invalid literature archive identifier or directory")
    path = folder / f"{search_id}.json"
    if path.is_symlink():
        raise LiteratureArchiveError("Refusing symlinked archive snapshot")
    try:
        if path.stat().st_size > MAX_ARCHIVE:
            raise LiteratureArchiveError("Retrieval archive too large")
        body = path.read_bytes()
        if digest_bytes(body) != search_id:
            raise LiteratureArchiveError("Archive content does not match immutable digest")
        data = json.loads(body.decode("utf-8"))
        if not isinstance(data, dict) or canonical_json(data) != body:
            raise LiteratureArchiveError("Noncanonical archive payload")
        if data.get("schema_version") != "1.0" or not isinstance(data.get("batches"), list):
            raise LiteratureArchiveError("Invalid archive schema")
        batches = [RetrievalBatch.model_validate_json(json.dumps(row)) for row in data["batches"]]
        if snapshot(batches) != data:
            raise LiteratureArchiveError("Archive canonical grouping or record mismatch")
        return data
    except (OSError, ValueError, UnicodeError, ValidationError) as exc:
        raise LiteratureArchiveError("Literature archive missing or invalid") from exc


def report_markdown(data: dict[str, Any]) -> str:
    rows = [
        "# ResearchForge LiteratureForge retrieval report",
        "",
        "These are provider observations, not independent validation or a global literature audit.",
        "",
    ]
    for batch in data["batches"]:
        rows.extend([
            f"## {batch['provider']} — {batch['kind']}",
            "",
            f"Query: {batch['query']}",
            f"Completeness: **{batch['completeness']}**",
            f"Retrieved: {batch['retrieved_total']}",
            f"Reported total: {batch['reported_total']}",
            f"Failure: {batch['failure_reason'] or 'none'}",
            f"Next cursor: {batch['next_cursor'] or 'none'}",
            "",
        ])
        for entry in batch["items"]:
            name = entry["title"] or "(title not supplied)"
            rows.append(
                f"- {name} — {entry['doi'] or 'no validated DOI'} "
                f"[{entry['provider']}:{entry['provider_id']}]"
            )
        rows.append("")
    rows.extend([
        "## Limitations",
        "",
        "- A missing DOI or provider result does not prove that a work is undiscoverable.",
        "- DOI matching never silently merges source-specific title, author, date or counts.",
        "- Truncated or failed pages remain PARTIAL or UNKNOWN with explicit failure metadata.",
        "",
    ])
    return "\n".join(rows)

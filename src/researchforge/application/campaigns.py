"""Frozen local campaign manifests with exact Git/source/data identities (PR03)."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlparse

from pydantic import ValidationError

from researchforge.application.evidence import (
    EventLedger,
    LedgerIntegrityError,
    SequenceConflict,
)
from researchforge.domain.models import CampaignManifest, FrozenManifest, FrozenSplit, SplitRole
from researchforge.domain.serialization import canonical_json, digest_bytes


class ManifestError(Exception):
    """Manifest is missing, invalid, changed, unsafe or not fully resolved."""


class FrozenStateError(Exception):
    """An existing lock disagrees with source or ledger evidence."""


def _safe_child(root: Path, candidate: str) -> Path:
    if not candidate or "\x00" in candidate:
        raise ManifestError("Invalid manifest-relative path")
    requested = Path(candidate)
    if requested.is_absolute():
        raise ManifestError("Absolute input paths are not allowed")
    result = (root / requested).resolve()
    if not result.is_relative_to(root) or not result.is_file():
        raise ManifestError("Missing input file or symlink escape")
    return result


def _read_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda n: _reject(n))
    except (ValueError, OSError, UnicodeError) as exc:
        raise ManifestError(f"Invalid JSON input: {path.name}") from exc


def _reject(value: str) -> None:
    raise ValueError(f"Non-finite JSON constant: {value}")


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            check=True,
            timeout=10,
        )
        return result.stdout.decode("ascii").strip()
    except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        raise ManifestError("Cannot resolve requested Git object") from exc


def _repository_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        raise ManifestError("PR03 only supports a trusted local file:// repository")
    root = Path(unquote(parsed.path)).resolve()
    if not root.is_dir():
        raise ManifestError("Local Git repository does not exist")
    return root


def _source_fingerprint(root: Path) -> tuple[dict[str, str], str]:
    """Fingerprint local evaluator Python source, excluding data and Git metadata."""
    source: dict[str, str] = {}
    for path in sorted(root.rglob("*.py")):
        if any(part in {".git", ".researchforge", ".venv"} for part in path.parts):
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(root) or not resolved.is_file():
            raise ManifestError("Evaluator source symlink escapes its root")
        if resolved.stat().st_size > 4 * 1024 * 1024:
            raise ManifestError("Evaluator source too large")
        source[str(path.relative_to(root))] = digest_bytes(resolved.read_bytes())
    if not source:
        raise ManifestError("Missing evaluator source .py files")
    return source, digest_bytes(canonical_json(source))


def _split_ids(path: Path) -> set[str]:
    raw = _read_json_file(path)
    if not isinstance(raw, dict) or not isinstance(raw.get("cases"), list):
        raise ManifestError("Each split needs a JSON object with a cases list")
    ids: set[str] = set()
    for row in raw["cases"]:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise ManifestError("Each split case needs a string id")
        item = row["id"]
        if not item or item in ids:
            raise ManifestError("Duplicate or empty sample ID within split")
        ids.add(item)
    if not ids:
        raise ManifestError("Empty split")
    return ids


def _atomic_publish(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".rf-lock-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError as exc:
            if path.is_symlink() or path.read_bytes() != content:
                raise FrozenStateError("Refusing overwrite of existing frozen manifest") from exc
        if os.name == "posix":
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


class CampaignService:
    def __init__(self, output: Path) -> None:
        self.root = output.resolve()
        self.manifest_path = self.root / "campaign.json"
        self.origin_path = self.root / "origin.json"
        self.lock_path = self.root / "manifest.lock.json"

    @classmethod
    def init(cls, manifest: Path, output: Path) -> CampaignService:
        """Create tool-owned folder, never overwrite an existing user directory."""
        source = manifest.resolve(strict=True)
        raw = source.read_bytes()
        try:
            CampaignManifest.model_validate_json(raw)
        except ValidationError as exc:
            raise ManifestError("Invalid campaign contract") from exc
        output.mkdir(parents=True, exist_ok=False)
        created = cls(output)
        created.manifest_path.write_bytes(raw)
        created.origin_path.write_bytes(
            canonical_json(
                {"manifest_origin": str(source.parent), "manifest_sha256": digest_bytes(raw)}
            )
        )
        return created

    def _manifest(self) -> tuple[CampaignManifest, Path, bytes]:
        try:
            origin = _read_json_file(self.origin_path)
            raw = self.manifest_path.read_bytes()
            if not isinstance(origin, dict):
                raise ManifestError("Malformed origin metadata")
            if digest_bytes(raw) != origin["manifest_sha256"]:
                raise ManifestError("Source manifest drift detected")
            manifest = CampaignManifest.model_validate_json(raw)
            origin_root = Path(origin["manifest_origin"]).resolve()
            if not origin_root.is_dir():
                raise ManifestError("Original evaluator directory no longer exists")
            return manifest, origin_root, raw
        except (OSError, KeyError, TypeError, ValidationError, ValueError) as exc:
            raise ManifestError("Campaign is uninitialized or manifest changed") from exc

    def validate(self) -> FrozenManifest:
        """Read-only resolve all inputs; never store a decision or mutate source."""
        manifest, evaluator_root, raw = self._manifest()
        repository = _repository_path(manifest.base.repository)
        commit = _git(
            repository, "rev-parse", "--verify", "--end-of-options",
            f"{manifest.base.commit}^{{commit}}"
        )
        tree = _git(repository, "rev-parse", "--verify", "--end-of-options", f"{commit}^{{tree}}")
        if len(commit) not in (40, 64) or len(tree) not in (40, 64):
            raise ManifestError("Git returned an invalid full OID")
        eval_sources, evaluator_digest = _source_fingerprint(evaluator_root)
        splits: dict[str, FrozenSplit] = {}
        seen: set[str] = set()
        for role in ("dev", "qualification", "final"):
            relative = getattr(manifest.splits, role)
            path = _safe_child(evaluator_root, relative)
            ids = _split_ids(path)
            if seen.intersection(ids):
                raise ManifestError("Sample IDs overlap across split roles")
            seen.update(ids)
            splits[role] = FrozenSplit(
                role=cast(SplitRole, role),
                relative_path=relative,
                sha256=digest_bytes(path.read_bytes()),
                case_count=len(ids)
            )
        # Require evaluator script inside known source. Shell commands are not run in PR03.
        if not any(
            token.endswith(".py") and token in eval_sources
            for token in manifest.evaluation.argv
        ):
            raise ManifestError("Evaluator argv must name a fingerprinted Python source file")
        environment = {"python": sys.version.split()[0], "platform": platform.system()}
        return FrozenManifest(
            schema_version="1.0",
            campaign_id=manifest.campaign_id,
            manifest_sha256=digest_bytes(raw),
            repository_commit=commit,
            repository_tree=tree,
            evaluator_source_sha256=evaluator_digest,
            evaluator_files=eval_sources,
            environment_sha256=digest_bytes(canonical_json(environment)),
            policy_sha256=digest_bytes(canonical_json(manifest.qualification_policy.model_dump(mode="json"))),
            splits=splits,
        )

    def freeze(self) -> FrozenManifest:
        snapshot = self.validate()
        locked = canonical_json(snapshot.model_dump(mode="json")) + b"\n"
        if self.lock_path.exists():
            if self.lock_path.is_symlink() or self.lock_path.read_bytes() != locked:
                raise FrozenStateError("An immutable campaign already exists with different inputs")
        else:
            _atomic_publish(self.lock_path, locked)
        ledger = EventLedger(self.root)
        # Recover conservative lock-only state after crash between durable publication
        # and DB commit. A second writer may win; recheck the completed evidence.
        if ledger.verify(snapshot.campaign_id)["event_count"] == 0:
            try:
                ledger.append_event(
                    snapshot.campaign_id,
                    0,
                    "CAMPAIGN_FROZEN",
                    snapshot.campaign_id,
                    {"lock_sha256": digest_bytes(locked)},
                )
            except SequenceConflict:
                pass
        self.inspect()
        return snapshot

    def inspect(self) -> dict[str, Any]:
        """Read-only lock integrity inspection; refuses drift or missing freeze event."""
        try:
            locked = self.lock_path.read_bytes()
            snapshot = FrozenManifest.model_validate_json(locked)
            if snapshot != self.validate():
                raise FrozenStateError("Source, evaluator, environment or split drift")
            ledger = EventLedger(self.root)
            state = ledger.verify(snapshot.campaign_id)
            events = ledger.read_events(snapshot.campaign_id)
            if not events or events[0].event_type != "CAMPAIGN_FROZEN":
                raise FrozenStateError("Missing campaign frozen event")
            payload = json.loads(ledger.cas.get(events[0].payload_ref))
            if payload != {"lock_sha256": digest_bytes(locked)}:
                raise FrozenStateError("Freeze event does not bind manifest.lock.json")
            return {"campaign": snapshot.model_dump(mode="json"), "ledger": state}
        except (OSError, ValidationError, LedgerIntegrityError, ValueError) as exc:
            raise FrozenStateError("Cannot verify the frozen campaign") from exc

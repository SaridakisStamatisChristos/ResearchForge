"""Immutable filesystem content-addressed storage with conservative crash behavior."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from researchforge.domain.models import ArtifactRef
from researchforge.domain.serialization import digest_bytes


class ArtifactIntegrityError(Exception):
    """Existing or loaded blob conflicts with its content address."""


class FileSystemCAS:
    """Store blobs under sha256/ab/cd/hash; never overwrite an existing address."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, digest: str) -> Path:
        return self.root / "sha256" / digest[:2] / digest[2:4] / digest

    @staticmethod
    def _sync_dir(path: Path) -> None:
        if os.name == "posix":
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def _checked_blob(self, path: Path, ref: ArtifactRef) -> bytes:
        if path.is_symlink() or not path.parent.resolve().is_relative_to(self.root):
            raise ArtifactIntegrityError("Symlink or escaped path at content address")
        try:
            data = path.read_bytes()
        except FileNotFoundError as exc:
            raise ArtifactIntegrityError("Missing blob") from exc
        if len(data) != ref.size_bytes or digest_bytes(data) != ref.sha256:
            raise ArtifactIntegrityError("Blob digest/length mismatch")
        return data

    def put(self, content: bytes) -> ArtifactRef:
        if not isinstance(content, bytes):
            raise TypeError("CAS only accepts immutable byte strings")
        ref = ArtifactRef(sha256=digest_bytes(content), size_bytes=len(content))
        target = self._path(ref.sha256)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.parent.resolve().is_relative_to(self.root):
            raise ArtifactIntegrityError("Content address escaped CAS root")
        if target.is_symlink():
            raise ArtifactIntegrityError("Existing address is a symlink")
        if target.exists():
            if self._checked_blob(target, ref) != content:
                raise ArtifactIntegrityError("Different bytes share an artifact address")
            return ref

        # A named temporary file is on the same filesystem as the destination.
        fd, temporary = tempfile.mkstemp(prefix=".rf-tmp-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                # Hard-link publication is atomic and does not replace a competing writer.
                os.link(temporary, target, follow_symlinks=False)
            except FileExistsError:
                pass
            self._sync_dir(target.parent)
            if self._checked_blob(target, ref) != content:
                raise ArtifactIntegrityError("Competing writer published different bytes")
        finally:
            Path(temporary).unlink(missing_ok=True)
        return ref

    def get(self, ref: ArtifactRef) -> bytes:
        return self._checked_blob(self._path(ref.sha256), ref)

    def verify(self, ref: ArtifactRef) -> bool:
        try:
            self.get(ref)
        except ArtifactIntegrityError:
            return False
        return True

    def all_digests(self) -> set[str]:
        """Inventory only. Never clean blobs automatically or follow symlinks."""
        base = self.root / "sha256"
        if not base.exists():
            return set()
        return {
            path.name
            for path in base.glob("*/*/*")
            if path.is_file() and not path.is_symlink() and len(path.name) == 64
        }

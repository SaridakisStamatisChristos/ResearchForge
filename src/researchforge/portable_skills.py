"""Portable, explicitly exported Agent Skills instructions (local-only PR07)."""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path
from typing import Any

NAME = "researchforge-experiment"


class PortableSkillError(ValueError):
    """Skill metadata is malformed, or the explicit export would overwrite user files."""


def _bundled_content() -> bytes:
    return (
        resources.files("researchforge").joinpath("portable_skills", NAME, "SKILL.md").read_bytes()
    )


def validate_skill(path: Path | None = None) -> dict[str, Any]:
    """Check the portable skill contract without network or external executables."""
    raw = path.read_bytes() if path is not None else _bundled_content()
    if len(raw) > 128_000:
        raise PortableSkillError("Skill exceeds size limit")
    try:
        content = raw.decode("utf-8")
    except UnicodeError as exc:
        raise PortableSkillError("Skill must be valid UTF-8") from exc
    match = re.match(r"\A---\n(.*?)\n---\n", content, flags=re.DOTALL)
    if match is None:
        raise PortableSkillError("Missing YAML frontmatter")
    header = match.group(1)
    if not re.search(r"(?m)^name:\s*researchforge-experiment\s*$", header):
        raise PortableSkillError("Unexpected skill name")
    if not re.search(r"(?m)^description:\s*\S+", header):
        raise PortableSkillError("Missing skill description")
    if not re.search(r"(?m)^compatibility:\s*\S+", header):
        raise PortableSkillError("Missing environment compatibility")
    if not re.search(r'(?m)^\s+version:\s*["\']?0\.1\.0["\']?\s*$', header):
        raise PortableSkillError("Missing version metadata")
    for requirement in (
        "rf demo run --offline",
        "rf campaign freeze",
        "rf qualify",
        "rf final nominate",
        "rf final evaluate",
        "rf verify",
    ):
        if requirement not in content:
            raise PortableSkillError(f"Missing executable usage instruction: {requirement}")
    if "SEE-REPOSITORY-LICENSE" in content:
        raise PortableSkillError("Unresolved license placeholder")
    return {"name": NAME, "version": "0.1.0", "bytes": len(raw), "valid": True}


def export_skill(target: Path) -> Path:
    """Copy a verified bundled skill ONLY into the user-specified new destination."""
    validate_skill()
    if target.is_symlink():
        raise PortableSkillError("Refusing a symlinked skill export directory")
    destination = target / NAME
    if destination.exists() or destination.is_symlink():
        raise PortableSkillError("Refusing to overwrite existing skill or plugin")
    target.mkdir(parents=True, exist_ok=True)
    destination.mkdir(exist_ok=False)
    skill = destination / "SKILL.md"
    try:
        with skill.open("xb") as handle:
            handle.write(_bundled_content())
        validate_skill(skill)
    except BaseException:
        skill.unlink(missing_ok=True)
        destination.rmdir()
        raise
    return destination

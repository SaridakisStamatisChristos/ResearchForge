"""Portable, opt-in Agent Skills packaging for ResearchForge modules."""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path
from typing import Any

NAME = "researchforge-experiment"
SUPPORTED_NAMES = (
    NAME,
    "researchforge-statistics",
    "researchforge-literature",
    "researchforge-audit",
)
REQUIRED_COMMANDS: dict[str, tuple[str, ...]] = {
    NAME: (
        "rf demo run --offline",
        "rf campaign freeze",
        "rf qualify",
        "rf final nominate",
        "rf final evaluate",
        "rf verify",
    ),
    "researchforge-statistics": ("rf stats paired", "--file", "--seed"),
    "researchforge-literature": (
        "rf literature lookup",
        "rf literature search",
        "rf literature report",
        "--allow-network",
    ),
    "researchforge-audit": ("rf audit claims", "--input", "--campaign", "--literature-store"),
}


class PortableSkillError(ValueError):
    """Skill metadata is invalid or export would overwrite a user resource."""


def _check_name(name: str) -> None:
    if name not in SUPPORTED_NAMES:
        raise PortableSkillError("Unknown bundled ResearchForge skill name")


def _bundled_content(name: str = NAME) -> bytes:
    _check_name(name)
    return (
        resources.files("researchforge").joinpath("portable_skills", name, "SKILL.md").read_bytes()
    )


def validate_skill(path: Path | None = None, *, name: str = NAME) -> dict[str, Any]:
    """Validate selected skill metadata and actual CLI instructions offline."""
    _check_name(name)
    raw = path.read_bytes() if path is not None else _bundled_content(name)
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
    if not re.search(r"(?m)^name:\s*" + re.escape(name) + r"\s*$", header):
        raise PortableSkillError("Unexpected skill name")
    if not re.search(r"(?m)^description:\s*\S+", header):
        raise PortableSkillError("Missing skill description")
    if not re.search(r"(?m)^compatibility:\s*\S+", header):
        raise PortableSkillError("Missing environment compatibility")
    if not re.search(r'(?m)^\s+version:\s*["\']?0\.1\.0["\']?\s*$', header):
        raise PortableSkillError("Missing version metadata")
    for requirement in REQUIRED_COMMANDS[name]:
        if requirement not in content:
            raise PortableSkillError(f"Missing executable usage instruction: {requirement}")
    if "SEE-REPOSITORY-LICENSE" in content:
        raise PortableSkillError("Unresolved license placeholder")
    return {"name": name, "version": "0.1.0", "bytes": len(raw), "valid": True}


def export_skill(target: Path, *, name: str = NAME) -> Path:
    """Explicitly export one skill; never replace existing host resources."""
    validate_skill(name=name)
    if any(part.is_symlink() for part in (target, *target.parents)):
        raise PortableSkillError("Refusing symlinked skill export directory or ancestor")
    destination = target / name
    if destination.exists() or destination.is_symlink():
        raise PortableSkillError("Refusing to overwrite existing skill or plugin")
    target.mkdir(parents=True, exist_ok=True)
    destination.mkdir(exist_ok=False)
    skill = destination / "SKILL.md"
    try:
        with skill.open("xb") as handle:
            handle.write(_bundled_content(name))
        validate_skill(skill, name=name)
    except BaseException:
        skill.unlink(missing_ok=True)
        destination.rmdir()
        raise
    return destination

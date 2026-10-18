"""PR10 skills packaging, name routing and no-overwrite regressions."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from researchforge.cli import app
from researchforge.portable_skills import (
    NAME,
    SUPPORTED_NAMES,
    PortableSkillError,
    export_skill,
    validate_skill,
)


@pytest.mark.parametrize("name", SUPPORTED_NAMES)
def test_each_skill_is_identical_to_source_and_exportable(tmp_path: Path, name: str) -> None:
    path = Path(__file__).resolve().parents[2] / "skills" / name / "SKILL.md"
    assert path.is_file()
    assert validate_skill(name=name) == validate_skill(path, name=name)
    assert CliRunner().invoke(app, ["skill", "validate", "--name", name]).exit_code == 0
    exported = export_skill(tmp_path / "skills", name=name)
    assert exported.joinpath("SKILL.md").read_bytes() == path.read_bytes()
    with pytest.raises(PortableSkillError, match="overwrite"):
        export_skill(tmp_path / "skills", name=name)


def test_explicit_exports_keep_existing_user_resources(tmp_path: Path) -> None:
    target = tmp_path / ".agents" / "skills"
    (target / "user-skill").mkdir(parents=True)
    precious = target / "user-skill" / "SKILL.md"
    precious.write_bytes(b"do not touch\n")
    runner = CliRunner()
    for name in SUPPORTED_NAMES:
        result = runner.invoke(
            app, ["skill", "export", "--name", name, "--target", str(target)]
        )
        assert result.exit_code == 0, result.output
    assert precious.read_bytes() == b"do not touch\n"
    assert runner.invoke(
        app, ["skill", "export", "--name", NAME, "--target", str(target)]
    ).exit_code == 3


def test_unknown_and_symlink_escape(tmp_path: Path) -> None:
    with pytest.raises(PortableSkillError, match="Unknown"):
        validate_skill(name="not-a-skill")
    assert CliRunner().invoke(
        app, ["skill", "validate", "--name", "not-a-skill"]
    ).exit_code == 2
    real = tmp_path / "real"
    real.mkdir()
    symlink = tmp_path / "alias"
    try:
        symlink.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks unavailable")
    with pytest.raises(PortableSkillError, match="symlink"):
        export_skill(symlink / "nested", name="researchforge-audit")
    assert not list(real.iterdir())

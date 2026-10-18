"""Smoke tests for the real Typer command surface."""

import json

from typer.testing import CliRunner

from researchforge.cli import app

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert "rf 0.1.0" in result.output


def test_schema_command() -> None:
    result = runner.invoke(app, ["contracts", "schema", "--name", "campaign"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["title"] == "CampaignManifest"


def test_error_exit_code_and_envelope() -> None:
    result = runner.invoke(app, ["contracts", "error-example"])
    assert result.exit_code == 2
    assert json.loads(result.output)["status"] == "INVALID_INPUT"

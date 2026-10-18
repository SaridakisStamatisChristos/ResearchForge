"""CLI surface only; research execution commands are added in subsequent PRs."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from researchforge import __version__
from researchforge.domain.envelope import ApiError, ResponseEnvelope
from researchforge.domain.models import CampaignManifest, EvaluationResult, EvidenceEvent

app = typer.Typer(no_args_is_help=True, add_completion=False)
contracts = typer.Typer(help="Inspect public, versioned contracts.")
app.add_typer(contracts, name="contracts")


def version_callback(value: bool) -> None:
    if value:
        typer.echo(f"rf {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool | None, typer.Option("--version", callback=version_callback, is_eager=True)
    ] = None,
) -> None:
    """ResearchForge deterministic research toolkit (PR01 foundation)."""
    del version


@contracts.command("schema")
def schema(
    name: Annotated[str, typer.Option("--name")],
) -> None:
    """Print the frozen v1 JSON-schema contract for a specified model."""
    models = {
        "campaign": CampaignManifest,
        "evaluator-result": EvaluationResult,
        "evidence-event": EvidenceEvent,
    }
    if name not in models:
        raise typer.BadParameter("choose campaign, evaluator-result, or evidence-event")
    typer.echo(json.dumps(models[name].model_json_schema(), indent=2, sort_keys=True))


@contracts.command("error-example")
def error_example() -> None:
    """Show the stable machine-readable error envelope; exit code 2 means invalid input."""
    envelope = ResponseEnvelope(
        status="INVALID_INPUT",
        errors=[ApiError(code="EXAMPLE", message="Example only; no operation attempted")],
    )
    typer.echo(envelope.model_dump_json())
    raise typer.Exit(code=2)


if __name__ == "__main__":
    app()

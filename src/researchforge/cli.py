"""CLI surface only; research execution commands are added in subsequent PRs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel

from researchforge import __version__
from researchforge.application.campaigns import CampaignService, FrozenStateError, ManifestError
from researchforge.domain.envelope import ApiError, ResponseEnvelope
from researchforge.domain.models import (
    CampaignManifest,
    EvaluationResult,
    EvidenceEvent,
    FrozenManifest,
)

app = typer.Typer(no_args_is_help=True, add_completion=False)
contracts = typer.Typer(help="Inspect public, versioned contracts.")
app.add_typer(contracts, name="contracts")
campaign = typer.Typer(help="Initialize, validate and freeze local campaigns.")
app.add_typer(campaign, name="campaign")


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
    models: dict[str, type[BaseModel]] = {
        "campaign": CampaignManifest,
        "evaluator-result": EvaluationResult,
        "evidence-event": EvidenceEvent,
        "manifest-lock": FrozenManifest,
    }
    if name not in models:
        raise typer.BadParameter(
            "choose campaign, evaluator-result, evidence-event, or manifest-lock"
        )
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


@campaign.command("init")
def campaign_init(
    manifest: Annotated[Path, typer.Option("--manifest")],
    output: Annotated[Path, typer.Option("--output")],
) -> None:
    """Initialize local campaign without executing any code."""
    try:
        service = CampaignService.init(manifest, output)
        typer.echo(f"Initialized {service.root}")
    except (ManifestError, FileExistsError, FileNotFoundError, OSError) as exc:
        typer.echo(f"INVALID_INPUT: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@campaign.command("validate")
def campaign_validate(
    campaign_path: Annotated[Path, typer.Option("--campaign")],
) -> None:
    """Inspect source/evaluator/split identities without mutating campaign state."""
    try:
        snapshot = CampaignService(campaign_path).validate()
        typer.echo(snapshot.model_dump_json(indent=2))
    except ManifestError as exc:
        typer.echo(f"INVALID_INPUT: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@campaign.command("freeze")
def campaign_freeze(
    campaign_path: Annotated[Path, typer.Option("--campaign")],
) -> None:
    """Bind an immutable manifest lock to a durable campaign event."""
    try:
        snapshot = CampaignService(campaign_path).freeze()
        typer.echo(f"FROZEN {snapshot.campaign_id} {snapshot.manifest_sha256}")
    except ManifestError as exc:
        typer.echo(f"INVALID_INPUT: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except FrozenStateError as exc:
        typer.echo(f"REFUSED: {exc}", err=True)
        raise typer.Exit(code=3) from exc


@campaign.command("inspect")
def campaign_inspect(
    campaign_path: Annotated[Path, typer.Option("--campaign")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Read-only integrity check on the frozen manifest and ledger."""
    try:
        data = CampaignService(campaign_path).inspect()
        if as_json:
            typer.echo(ResponseEnvelope(status="OK", data=data).model_dump_json(indent=2))
        else:
            typer.echo(json.dumps(data, indent=2, sort_keys=True))
    except (ManifestError, FrozenStateError) as exc:
        typer.echo(f"CORRUPT: {exc}", err=True)
        raise typer.Exit(code=6) from exc


if __name__ == "__main__":
    app()

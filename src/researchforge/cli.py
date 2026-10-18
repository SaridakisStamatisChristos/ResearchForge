"""CLI surface only; research execution commands are added in subsequent PRs."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import BaseModel, ValidationError

from researchforge import __version__
from researchforge.application.campaigns import CampaignService, FrozenStateError, ManifestError
from researchforge.application.final import FinalService
from researchforge.application.qualification import HypothesisSpec, QualificationService
from researchforge.application.runner import (
    ExecutionRefused,
    RunnerIntegrityError,
    RunnerService,
    _pairs_unique,
    _reject_constant,
)
from researchforge.demo import DemoError, run_demo
from researchforge.domain.envelope import ApiError, ResponseEnvelope
from researchforge.domain.models import (
    CampaignManifest,
    EvaluationResult,
    EvidenceEvent,
    FrozenManifest,
)
from researchforge.literature import (
    LiteratureInputError,
    LiteratureQuery,
    parse_sources,
    run_lookup,
    run_search,
)
from researchforge.literature_archive import (
    LiteratureArchiveError,
    read_snapshot,
    report_markdown,
    snapshot,
    store_snapshot,
)
from researchforge.portable_skills import (
    NAME as PORTABLE_SKILL_NAME,
)
from researchforge.portable_skills import (
    PortableSkillError,
    export_skill,
    validate_skill,
)
from researchforge.statistics import StatisticalInputError, paired_comparison

app = typer.Typer(no_args_is_help=True, add_completion=False)
contracts = typer.Typer(help="Inspect public, versioned contracts.")
app.add_typer(contracts, name="contracts")
campaign = typer.Typer(help="Initialize, validate and freeze local campaigns.")
app.add_typer(campaign, name="campaign")
run_commands = typer.Typer(help="Trusted-local evaluator execution; not a sandbox.")
app.add_typer(run_commands, name="run")
hypothesis_commands = typer.Typer(help="Record falsifiable hypotheses.")
app.add_typer(hypothesis_commands, name="hypothesis")
candidate_commands = typer.Typer(help="Register Git-identified candidates.")
app.add_typer(candidate_commands, name="candidate")
final_commands = typer.Typer(help="One-shot pre-registered final evaluation.")
app.add_typer(final_commands, name="final")
stats_commands = typer.Typer(help="Deterministic descriptive statistical comparisons.")
app.add_typer(stats_commands, name="stats")
demo_commands = typer.Typer(help="Real, fully offline trusted-toy demonstration.")
app.add_typer(demo_commands, name="demo")
skill_commands = typer.Typer(help="Validate or explicitly export portable Agent Skills.")
app.add_typer(skill_commands, name="skill")
literature_commands = typer.Typer(help="Source-specific scholarly metadata retrieval.")
app.add_typer(literature_commands, name="literature")


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


@run_commands.command("evaluate")
def run_evaluate(
    campaign_path: Annotated[Path, typer.Option("--campaign")],
    split: Annotated[str, typer.Option("--split")],
    approve_trusted_local: Annotated[bool, typer.Option("--approve-trusted-local")] = False,
    candidate_id: Annotated[str | None, typer.Option("--candidate")] = None,
) -> None:
    """Run an explicitly approved dev/qualification evaluator (never final)."""
    try:
        outcome = RunnerService(campaign_path).run(
            role=split, approved=approve_trusted_local, candidate_id=candidate_id
        )
        state = outcome["status"]
        response = ResponseEnvelope(
            status="OK" if state == "SUCCEEDED" else "UNKNOWN" if state == "UNKNOWN" else "FAILED",
            data=outcome,
        )
        typer.echo(response.model_dump_json())
        if state != "SUCCEEDED":
            raise typer.Exit(code=5 if state == "UNKNOWN" else 4)
    except ExecutionRefused as exc:
        typer.echo(f"REFUSED: {exc}", err=True)
        raise typer.Exit(code=3) from exc
    except RunnerIntegrityError as exc:
        typer.echo(f"CORRUPT: {exc}", err=True)
        raise typer.Exit(code=6) from exc


@run_commands.command("recover")
def run_recover(
    campaign_path: Annotated[Path, typer.Option("--campaign")],
    mark_unknown: Annotated[bool, typer.Option("--mark-unknown")] = False,
) -> None:
    """Mark interrupted attempts UNKNOWN; never auto-replay them."""
    try:
        ids = RunnerService(campaign_path).recover_unknown(approved=mark_unknown)
        typer.echo(ResponseEnvelope(status="OK", data={"marked_unknown": ids}).model_dump_json())
    except ExecutionRefused as exc:
        typer.echo(f"REFUSED: {exc}", err=True)
        raise typer.Exit(code=3) from exc
    except RunnerIntegrityError as exc:
        typer.echo(f"CORRUPT: {exc}", err=True)
        raise typer.Exit(code=6) from exc


@hypothesis_commands.command("add")
def hypothesis_add(
    campaign_path: Annotated[Path, typer.Option("--campaign")],
    file: Annotated[Path, typer.Option("--file")],
) -> None:
    """Append a human-authored hypothesis; never execute it."""
    try:
        spec = HypothesisSpec.model_validate_json(file.read_bytes())
        outcome = QualificationService(campaign_path).add_hypothesis(spec)
        typer.echo(ResponseEnvelope(status="OK", data=outcome).model_dump_json())
    except (OSError, ValueError, ValidationError) as exc:
        typer.echo(f"INVALID_INPUT: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except ExecutionRefused as exc:
        typer.echo(f"REFUSED: {exc}", err=True)
        raise typer.Exit(code=3) from exc
    except RunnerIntegrityError as exc:
        typer.echo(f"CORRUPT: {exc}", err=True)
        raise typer.Exit(code=6) from exc


@candidate_commands.command("register")
def candidate_register(
    campaign_path: Annotated[Path, typer.Option("--campaign")],
    candidate_id: Annotated[str, typer.Option("--id")],
    hypothesis_id: Annotated[str, typer.Option("--hypothesis")],
    commit: Annotated[str, typer.Option("--commit")],
) -> None:
    """Pin an existing full Git commit without updating source branches."""
    try:
        outcome = QualificationService(campaign_path).register(
            candidate_id=candidate_id, hypothesis_id=hypothesis_id, commit=commit
        )
        typer.echo(ResponseEnvelope(status="OK", data=outcome).model_dump_json())
    except (ExecutionRefused, ManifestError) as exc:
        typer.echo(f"REFUSED: {exc}", err=True)
        raise typer.Exit(code=3) from exc
    except RunnerIntegrityError as exc:
        typer.echo(f"CORRUPT: {exc}", err=True)
        raise typer.Exit(code=6) from exc


@app.command("qualify")
def qualify(
    campaign_path: Annotated[Path, typer.Option("--campaign")],
    candidate_id: Annotated[str, typer.Option("--candidate")],
) -> None:
    """Replay evidence and append an immutable selection-stage decision."""
    try:
        outcome = QualificationService(campaign_path).qualify(candidate_id)
        typer.echo(ResponseEnvelope(status="OK", data=outcome).model_dump_json())
    except ExecutionRefused as exc:
        typer.echo(f"REFUSED: {exc}", err=True)
        raise typer.Exit(code=3) from exc
    except RunnerIntegrityError as exc:
        typer.echo(f"CORRUPT: {exc}", err=True)
        raise typer.Exit(code=6) from exc


@final_commands.command("nominate")
def final_nominate(
    campaign_path: Annotated[Path, typer.Option("--campaign")],
    candidate_id: Annotated[str, typer.Option("--candidate")],
) -> None:
    """Freeze the selected candidate and comparison without accessing final scores."""
    try:
        data = FinalService(campaign_path).nominate(candidate_id)
        typer.echo(ResponseEnvelope(status="OK", data=data).model_dump_json())
    except ExecutionRefused as exc:
        typer.echo(f"REFUSED: {exc}", err=True)
        raise typer.Exit(code=3) from exc
    except RunnerIntegrityError as exc:
        typer.echo(f"CORRUPT: {exc}", err=True)
        raise typer.Exit(code=6) from exc


@final_commands.command("evaluate")
def final_evaluate(
    campaign_path: Annotated[Path, typer.Option("--campaign")],
    approve_trusted_local: Annotated[bool, typer.Option("--approve-trusted-local")] = False,
) -> None:
    """Consume the single final evaluation right (baseline then nominated candidate)."""
    try:
        data = FinalService(campaign_path).evaluate(approved=approve_trusted_local)
        typer.echo(ResponseEnvelope(status="OK", data=data).model_dump_json())
    except ExecutionRefused as exc:
        typer.echo(f"REFUSED: {exc}", err=True)
        raise typer.Exit(code=3) from exc
    except RunnerIntegrityError as exc:
        typer.echo(f"CORRUPT: {exc}", err=True)
        raise typer.Exit(code=6) from exc


@final_commands.command("inspect")
def final_inspect(
    campaign_path: Annotated[Path, typer.Option("--campaign")],
) -> None:
    """Replay final plan and conclusion without executing experiments."""
    try:
        data = FinalService(campaign_path).inspect()
        typer.echo(ResponseEnvelope(status="OK", data=data).model_dump_json())
    except RunnerIntegrityError as exc:
        typer.echo(f"CORRUPT: {exc}", err=True)
        raise typer.Exit(code=6) from exc


@app.command("verify")
def verify_campaign(
    campaign_path: Annotated[Path, typer.Option("--campaign")],
) -> None:
    """Offline replay of ledger, frozen identities and any final conclusion."""
    try:
        data = FinalService(campaign_path).inspect()
        typer.echo(ResponseEnvelope(status="OK", data=data).model_dump_json())
    except RunnerIntegrityError as exc:
        typer.echo(f"CORRUPT: {exc}", err=True)
        raise typer.Exit(code=6) from exc


@app.command("report")
def report_campaign(
    campaign_path: Annotated[Path, typer.Option("--campaign")],
    report_format: Annotated[str, typer.Option("--format")] = "md",
    output: Annotated[Path | None, typer.Option("--output")] = None,
) -> None:
    """Read-only evidence replay; optional new report file is never overwritten."""
    try:
        service = FinalService(campaign_path)
        if report_format == "md":
            content = service.report_markdown()
        elif report_format == "json":
            content = json.dumps(service.inspect(), sort_keys=True, indent=2) + "\n"
        else:
            raise ValueError("Choose md or json report format")
        if output is None:
            typer.echo(content, nl=False)
        else:
            with output.open("x", encoding="utf-8") as handle:
                handle.write(content)
            typer.echo(str(output))
    except (OSError, ValueError) as exc:
        typer.echo(f"INVALID_INPUT: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except RunnerIntegrityError as exc:
        typer.echo(f"CORRUPT: {exc}", err=True)
        raise typer.Exit(code=6) from exc


@stats_commands.command("paired")
def stats_paired(
    file: Annotated[Path, typer.Option("--file")],
    seed: Annotated[int, typer.Option("--seed")],
    resamples: Annotated[int, typer.Option("--resamples")] = 2000,
) -> None:
    """Statistical description of explicitly aligned case-level measurements."""
    try:
        input_data = json.loads(
            file.read_text(encoding="utf-8"),
            object_pairs_hook=_pairs_unique,
            parse_constant=_reject_constant,
        )
        result = paired_comparison(
            input_data["baseline"],
            input_data["candidate"],
            direction=input_data["direction"],
            seed=seed,
            resamples=resamples,
        )
        typer.echo(ResponseEnvelope(status="OK", data=dict(result)).model_dump_json())
    except (OSError, ValueError, KeyError, TypeError, StatisticalInputError) as exc:
        typer.echo(f"INVALID_INPUT: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@demo_commands.command("run")
def demo_run(
    output: Annotated[Path, typer.Option("--output")],
    offline: Annotated[bool, typer.Option("--offline")] = False,
) -> None:
    """Execute the known trusted toy fixture without remote source/API requests."""
    if not offline:
        typer.echo("REFUSED: demo requires explicit --offline", err=True)
        raise typer.Exit(code=3)
    try:
        result = run_demo(output)
        typer.echo(ResponseEnvelope(status="OK", data=result).model_dump_json(indent=2))
    except (FileExistsError, OSError, ValueError) as exc:
        typer.echo(f"INVALID_INPUT: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except (DemoError, ExecutionRefused) as exc:
        typer.echo(f"FAILED: {exc}", err=True)
        raise typer.Exit(code=4) from exc
    except RunnerIntegrityError as exc:
        typer.echo(f"CORRUPT: {exc}", err=True)
        raise typer.Exit(code=6) from exc


@skill_commands.command("validate")
def skill_validate(
    name: Annotated[str, typer.Option("--name")] = PORTABLE_SKILL_NAME,
) -> None:
    """Validate bundled skill metadata and live CLI references, without network."""
    try:
        if name != PORTABLE_SKILL_NAME:
            raise PortableSkillError("Unknown bundled skill name")
        result = validate_skill()
        typer.echo(ResponseEnvelope(status="OK", data=result).model_dump_json())
    except (PortableSkillError, OSError) as exc:
        typer.echo(f"INVALID_INPUT: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@skill_commands.command("export")
def skill_export(
    target: Annotated[Path, typer.Option("--target")],
    name: Annotated[str, typer.Option("--name")] = PORTABLE_SKILL_NAME,
) -> None:
    """Export only to an explicit destination; never replace existing skills."""
    try:
        if name != PORTABLE_SKILL_NAME:
            raise PortableSkillError("Unknown bundled skill name")
        location = export_skill(target)
        typer.echo(
            ResponseEnvelope(
                status="OK", data={"name": name, "path": str(location)}
            ).model_dump_json()
        )
    except (PortableSkillError, OSError) as exc:
        typer.echo(f"REFUSED: {exc}", err=True)
        raise typer.Exit(code=3) from exc


def _literature_output(batches: list[Any], store: Path | None, as_json: bool) -> None:
    data = snapshot(batches)
    if store is not None:
        data["search_id"] = store_snapshot(store, batches)
    incomplete = any(batch.completeness != "COMPLETE" for batch in batches)
    if as_json:
        typer.echo(
            ResponseEnvelope(
                status="UNKNOWN" if incomplete else "OK",
                data=data,
            ).model_dump_json(indent=2)
        )
    else:
        for batch in batches:
            typer.echo(
                f"{batch.provider}: {batch.completeness}, "
                f"{batch.retrieved_total} records; "
                f"reason={batch.failure_reason or 'none'}"
            )
        if store is not None:
            typer.echo(f"search_id={data['search_id']}")
    if incomplete:
        raise typer.Exit(code=5)


@literature_commands.command("lookup")
def literature_lookup(
    doi: Annotated[str, typer.Option("--doi")],
    sources: Annotated[str, typer.Option("--sources")] = "crossref,openalex",
    as_json: Annotated[bool, typer.Option("--json")] = False,
    allow_network: Annotated[bool, typer.Option("--allow-network")] = False,
    store: Annotated[Path | None, typer.Option("--store")] = None,
) -> None:
    """Resolve a DOI via named sources, with explicit live-network approval."""
    try:
        selected = parse_sources(sources)
        if not allow_network:
            raise LiteratureInputError("Live literature retrieval requires --allow-network")
        results = asyncio.run(run_lookup(doi, sources=selected, allow_network=True))
        _literature_output(results, store, as_json)
    except (LiteratureInputError, LiteratureArchiveError, OSError) as exc:
        typer.echo(f"INVALID_INPUT: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@literature_commands.command("search")
def literature_search(
    query: Annotated[str, typer.Option("--query")],
    sources: Annotated[str, typer.Option("--sources")] = "openalex",
    limit: Annotated[int, typer.Option("--limit")] = 20,
    kind: Annotated[str, typer.Option("--kind")] = "works",
    as_json: Annotated[bool, typer.Option("--json")] = False,
    allow_network: Annotated[bool, typer.Option("--allow-network")] = False,
    store: Annotated[Path | None, typer.Option("--store")] = None,
) -> None:
    """Bounded, source-specific scholarly search with explicit network opt-in."""
    try:
        selected = parse_sources(sources)
        search = LiteratureQuery.model_validate({"text": query, "kind": kind, "limit": limit})
        if kind == "authors" and "crossref" in selected:
            raise LiteratureInputError("Author search requires --sources openalex")
        if not allow_network:
            raise LiteratureInputError("Live literature retrieval requires --allow-network")
        results = asyncio.run(run_search(search, sources=selected, allow_network=True))
        _literature_output(results, store, as_json)
    except (LiteratureInputError, LiteratureArchiveError, OSError, ValidationError) as exc:
        typer.echo(f"INVALID_INPUT: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@literature_commands.command("report")
def literature_report(
    search_id: Annotated[str, typer.Option("--search-id")],
    store: Annotated[Path, typer.Option("--store")],
    report_format: Annotated[str, typer.Option("--format")] = "md",
    output: Annotated[Path | None, typer.Option("--output")] = None,
) -> None:
    """Offline, integrity-checked source report; never retrieve live data."""
    try:
        data = read_snapshot(store, search_id)
        if report_format == "json":
            formatted = json.dumps(data, sort_keys=True, indent=2) + "\n"
        elif report_format == "md":
            formatted = report_markdown(data)
        else:
            raise LiteratureInputError("Choose --format md or json")
        if output is None:
            typer.echo(formatted, nl=False)
        else:
            with output.open("x", encoding="utf-8") as handle:
                handle.write(formatted)
            typer.echo(str(output))
    except (LiteratureArchiveError, LiteratureInputError, OSError) as exc:
        typer.echo(f"INVALID_INPUT: {exc}", err=True)
        raise typer.Exit(code=2) from exc


if __name__ == "__main__":
    app()

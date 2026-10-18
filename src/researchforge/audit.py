"""ResearchAuditor: bounded, read-only, deterministic claim/evidence checks.

A ledger event, a source hit and a scientifically supported result are distinct.
This module makes *mechanical* findings; it is not a peer reviewer or a proof of
novelty. The caller supplies claim text and exact evidence identities.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal

import typer
from pydantic import Field, ValidationError, model_validator

from researchforge.application.evidence import EventLedger, LedgerIntegrityError
from researchforge.application.final import FinalService
from researchforge.application.runner import RunnerIntegrityError
from researchforge.domain.envelope import ResponseEnvelope
from researchforge.domain.models import StrictModel
from researchforge.literature import LiteratureInputError, normalize_doi
from researchforge.literature_archive import LiteratureArchiveError, read_snapshot

ClaimKind = Literal["final_status", "qualification_status", "literature_doi"]
AuditStatus = Literal[
    "OBSERVED", "VERIFIED", "CONTRADICTED", "INCONCLUSIVE", "UNVERIFIED", "NOT_EVALUATED"
]
MAX_INPUT_BYTES = 1_048_576


class AuditInputError(ValueError):
    """A claim file or a requested evidence reference is invalid."""


class EvidenceLink(StrictModel):
    source: Literal["campaign_event", "literature_snapshot"]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ClaimSpec(StrictModel):
    claim_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    text: str = Field(min_length=1, max_length=2000)
    scope: str = Field(min_length=1, max_length=1000)
    kind: ClaimKind
    expected: str = Field(min_length=1, max_length=512)
    subject_id: str | None = Field(default=None, max_length=512)
    evidence: list[EvidenceLink] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def validate_assertion(self) -> ClaimSpec:
        if self.kind == "final_status":
            if (
                self.expected
                not in {"FINAL_SUPPORTED", "FINAL_NOT_SUPPORTED", "FINAL_INCONCLUSIVE"}
                or self.subject_id is not None
            ):
                raise ValueError("Final claim requires a final status and no subject_id")
        elif self.kind == "qualification_status":
            if self.expected not in {"QUALIFIED", "REJECTED", "INCONCLUSIVE"}:
                raise ValueError("Invalid qualification status")
            if not self.subject_id:
                raise ValueError("Qualification claim requires a candidate subject_id")
        else:
            normalize_doi(self.expected)
        allowed_source = (
            "literature_snapshot" if self.kind == "literature_doi" else "campaign_event"
        )
        if any(ref.source != allowed_source for ref in self.evidence):
            raise ValueError("Evidence source does not match claim kind")
        if len({ref.sha256 for ref in self.evidence}) != len(self.evidence):
            raise ValueError("Duplicate evidence reference")
        return self


class ClaimBatch(StrictModel):
    schema_version: Literal["1.0"]
    claims: list[ClaimSpec] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def check_ids(self) -> ClaimBatch:
        ids = [claim.claim_id for claim in self.claims]
        if len(ids) != len(set(ids)):
            raise ValueError("Claim IDs must be unique")
        return self


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("Duplicate JSON key")
        output[key] = value
    return output


def _reject_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON value: {value}")


def read_claims(path: Path) -> ClaimBatch:
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise AuditInputError("Claim file exceeds maximum size")
    try:
        content = path.read_bytes().decode("utf-8")
        obj = json.loads(content, object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)
        return ClaimBatch.model_validate(obj)
    except (ValueError, UnicodeError, ValidationError) as exc:
        raise AuditInputError("Invalid versioned claim input") from exc


def _campaign_evidence(
    campaign: Path,
) -> tuple[dict[str, Any], dict[str, tuple[str, str, dict[str, Any]]]]:
    # FinalService.inspects the immutable manifest and independently replays
    # final results from raw evaluator stdout, rather than trusting stored scores.
    final = FinalService(campaign).inspect()
    campaign_id = str(final["campaign_id"])
    ledger = EventLedger(campaign)
    ledger.verify(campaign_id)
    by_hash: dict[str, tuple[str, str, dict[str, Any]]] = {}
    for event in ledger.read_events(campaign_id):
        payload = json.loads(ledger.cas.get(event.payload_ref))
        if not isinstance(payload, dict):
            raise LedgerIntegrityError("Event payload must be an object")
        by_hash[event.event_sha256] = (event.event_type, event.subject_id, payload)
    return final, by_hash


def _result(
    claim: ClaimSpec,
    status: AuditStatus,
    *,
    used: list[str] | None = None,
    missing: list[str] | None = None,
    conflicts: list[str] | None = None,
    note: str = "",
) -> dict[str, Any]:
    return {
        "claim_id": claim.claim_id,
        "text": claim.text,
        "scope": claim.scope,
        "kind": claim.kind,
        "expected": claim.expected,
        "status": status,
        "used_evidence": used or [],
        "missing_evidence": missing or [],
        "conflicts": conflicts or [],
        "note": note,
    }


def _campaign_claim(
    claim: ClaimSpec,
    final: dict[str, Any] | None,
    events: dict[str, tuple[str, str, dict[str, Any]]],
) -> dict[str, Any]:
    if final is None:
        return _result(claim, "NOT_EVALUATED", missing=["campaign_not_supplied"])
    if not claim.evidence:
        return _result(claim, "INCONCLUSIVE", missing=["campaign_event_reference"])
    missing = [link.sha256 for link in claim.evidence if link.sha256 not in events]
    if missing:
        return _result(claim, "UNVERIFIED", missing=missing, note="Unknown campaign event hash")
    expected_event = "FINAL_EVALUATED" if claim.kind == "final_status" else "QUALIFICATION_DECIDED"
    matching = [
        (ref.sha256, events[ref.sha256])
        for ref in claim.evidence
        if events[ref.sha256][0] == expected_event
        and (claim.kind != "qualification_status" or events[ref.sha256][1] == claim.subject_id)
    ]
    if len(matching) != 1 or len(claim.evidence) != 1:
        return _result(
            claim, "UNVERIFIED", note="Evidence has wrong event type, subject or multiplicity"
        )
    sha, (_, _, payload) = matching[0]
    recorded = payload.get("status")
    if not isinstance(recorded, str):
        return _result(claim, "UNVERIFIED", used=[sha], note="Missing recorded status")
    if claim.kind == "final_status":
        verified = final.get("final_status")
        if (
            final.get("final_result") is None
            or recorded != verified
            or recorded not in {"FINAL_SUPPORTED", "FINAL_NOT_SUPPORTED", "FINAL_INCONCLUSIVE"}
        ):
            return _result(
                claim,
                "UNVERIFIED",
                used=[sha],
                note="Final event contradicts independently replayed final result",
            )
        if claim.expected != recorded:
            return _result(
                claim,
                "CONTRADICTED",
                used=[sha],
                conflicts=[f"actual={recorded}"],
                note="Contradicts the frozen final decision, not necessarily a scientific theory",
            )
        return _result(
            claim,
            "VERIFIED",
            used=[sha],
            note="Only the typed frozen final-status predicate was replayed; claim prose is not validated",
        )
    # The append-only ledger validates identity and integrity, but does not
    # independently establish statistical or scientific merit.
    latest = next(
        (
            ref
            for ref, (kind, subject, _) in reversed(list(events.items()))
            if kind == "QUALIFICATION_DECIDED" and subject == claim.subject_id
        ),
        None,
    )
    if sha != latest:
        return _result(
            claim,
            "UNVERIFIED",
            used=[sha],
            note="Superseded qualification evidence cannot be cherry-picked",
        )
    if recorded not in {"QUALIFIED", "REJECTED", "INCONCLUSIVE"}:
        return _result(claim, "UNVERIFIED", used=[sha])
    if recorded != claim.expected:
        return _result(
            claim,
            "CONTRADICTED",
            used=[sha],
            conflicts=[f"actual={recorded}"],
            note="Contradicts the recorded qualification decision",
        )
    return _result(
        claim,
        "OBSERVED",
        used=[sha],
        note="A qualification decision was recorded; it is not confirmatory evidence",
    )


def _literature_claim(claim: ClaimSpec, store: Path | None) -> dict[str, Any]:
    if store is None:
        return _result(claim, "NOT_EVALUATED", missing=["literature_store_not_supplied"])
    if not claim.evidence:
        return _result(claim, "INCONCLUSIVE", missing=["literature_snapshot_reference"])
    doi = normalize_doi(claim.expected)
    matches: list[str] = []
    for link in claim.evidence:
        try:
            data = read_snapshot(store, link.sha256)
        except LiteratureArchiveError:
            return _result(
                claim,
                "UNVERIFIED",
                missing=[link.sha256],
                note="Snapshot missing, corrupt or internally inconsistent",
            )
        for batch in data["batches"]:
            for item in batch["items"]:
                if item["doi"] == doi and (
                    claim.subject_id is None or item["provider_id"] == claim.subject_id
                ):
                    matches.append(link.sha256)
    if not matches:
        return _result(
            claim,
            "INCONCLUSIVE",
            note="No matching observation in supplied snapshots; absence is not established",
        )
    return _result(
        claim,
        "OBSERVED",
        used=sorted(set(matches)),
        note=(
            "Source metadata observation only; not claim truth, peer review "
            "or global discoverability"
        ),
    )


def audit_claims(
    batch: ClaimBatch,
    *,
    campaign: Path | None = None,
    literature_store: Path | None = None,
) -> dict[str, Any]:
    final: dict[str, Any] | None = None
    events: dict[str, tuple[str, str, dict[str, Any]]] = {}
    if campaign is not None:
        final, events = _campaign_evidence(campaign)
    entries = [
        _literature_claim(claim, literature_store)
        if claim.kind == "literature_doi"
        else _campaign_claim(claim, final, events)
        for claim in batch.claims
    ]
    counts = {
        status: sum(item["status"] == status for item in entries)
        for status in (
            "OBSERVED",
            "VERIFIED",
            "CONTRADICTED",
            "INCONCLUSIVE",
            "UNVERIFIED",
            "NOT_EVALUATED",
        )
    }
    return {
        "schema_version": "1.0",
        "campaign_id": final["campaign_id"] if final is not None else None,
        "claims": entries,
        "counts": counts,
        "limitations": [
            "A mechanically verified final predicate is not independent scientific validation",
            "Free-text claim wording is not semantically checked by the auditor",
            "A qualified candidate is not a confirmatory success",
            "Scholarly source metadata is not evidence that paper claims are correct",
            "Hash-chain and snapshot integrity do not defeat a malicious local administrator",
            "No outside data, network, LLM, or human novelty judgment was used",
        ],
    }


def markdown_report(report: dict[str, Any]) -> str:
    rows = [
        "# ResearchForge claim-evidence audit",
        "",
        "Mechanical checks only; this is not peer review or scientific certification.",
        "",
    ]
    for item in report["claims"]:
        rows.extend(
            [
                f"## {item['claim_id']} — {item['status']}",
                "",
                f"Claim: {item['text']}",
                f"Scope: {item['scope']}",
                f"Expected: {item['expected']}",
                f"Evidence: {', '.join(item['used_evidence']) or 'none'}",
                f"Missing: {', '.join(item['missing_evidence']) or 'none'}",
                f"Conflicts: {', '.join(item['conflicts']) or 'none'}",
                f"Interpretation: {item['note']}",
                "",
            ]
        )
    rows.extend(["## Limitations", ""])
    rows.extend(f"- {limitation}" for limitation in report["limitations"])
    return "\n".join(rows) + "\n"


audit_commands = typer.Typer(help="Offline mechanical claim-to-evidence auditing.")


@audit_commands.command("claims")
def audit_claims_cli(
    input_file: Annotated[Path, typer.Option("--input")],
    campaign: Annotated[Path | None, typer.Option("--campaign")] = None,
    literature_store: Annotated[Path | None, typer.Option("--literature-store")] = None,
    report_format: Annotated[str, typer.Option("--format")] = "json",
    output: Annotated[Path | None, typer.Option("--output")] = None,
) -> None:
    """Inspect supplied explicit claims; never execute evaluators or contact providers."""
    try:
        batch = read_claims(input_file)
        result = audit_claims(batch, campaign=campaign, literature_store=literature_store)
        if report_format == "json":
            rendered = ResponseEnvelope(status="OK", data=result).model_dump_json(indent=2) + "\n"
        elif report_format == "md":
            rendered = markdown_report(result)
        else:
            raise AuditInputError("Choose --format json or md")
        if output is None:
            typer.echo(rendered, nl=False)
        else:
            with output.open("x", encoding="utf-8") as stream:
                stream.write(rendered)
            typer.echo(str(output))
    except (AuditInputError, ValidationError, LiteratureInputError, OSError) as exc:
        typer.echo(f"INVALID_INPUT: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    except (RunnerIntegrityError, LedgerIntegrityError) as exc:
        typer.echo(f"CORRUPT: {exc}", err=True)
        raise typer.Exit(code=6) from exc

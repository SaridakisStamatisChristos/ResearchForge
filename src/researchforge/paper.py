"""PaperForge: offline, evidence-gated scientific manuscript export.

This is a mechanical draft, NOT a literature review, novelty determination,
statistical certification, LLM manuscript, or semantic validation of free text.
Only independently replayed final-predicate evidence populates measured tables.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError, field_validator

from researchforge.application.evidence import EventLedger
from researchforge.application.final import FinalService
from researchforge.audit import ClaimBatch, audit_claims
from researchforge.domain.models import StrictModel
from researchforge.domain.serialization import canonical_json, digest_bytes
from researchforge.literature_archive import LiteratureArchiveError, read_snapshot

MAX_PAPER_INPUT_BYTES = 1_048_576


class PaperInputError(ValueError):
    """Invalid versioned manuscript input or requested operation."""


class PaperIntegrityError(ValueError):
    """A supposedly verified evidence link cannot be reproduced."""


class PaperSpec(StrictModel):
    schema_version: Literal["1.0"]
    title: str = Field(min_length=3, max_length=200)
    authors: list[str] = Field(default_factory=list, max_length=20)
    claims: ClaimBatch

    @field_validator("title")
    @classmethod
    def valid_title(cls, value: str) -> str:
        if not value.strip() or any(ord(ch) < 32 for ch in value):
            raise ValueError("Title must be nonblank, single-line text")
        return value

    @field_validator("authors")
    @classmethod
    def valid_authors(cls, value: list[str]) -> list[str]:
        if any(
            not name.strip() or len(name) > 120 or any(ord(ch) < 32 for ch in name)
            for name in value
        ):
            raise ValueError("Authors must be nonblank single-line names")
        return value


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise PaperInputError("Duplicate JSON object key")
        output[key] = value
    return output


def _reject_constant(value: str) -> None:
    raise PaperInputError(f"Non-finite JSON value: {value}")


def read_paper_spec(path: Path) -> PaperSpec:
    if path.stat().st_size > MAX_PAPER_INPUT_BYTES:
        raise PaperInputError("Paper specification exceeds 1 MiB")
    try:
        data = json.loads(
            path.read_bytes().decode("utf-8"),
            object_pairs_hook=_unique_pairs,
            parse_constant=_reject_constant,
        )
        return PaperSpec.model_validate(data)
    except (UnicodeError, ValueError, ValidationError) as exc:
        raise PaperInputError("Invalid versioned PaperForge specification") from exc


def _event_payloads(campaign: Path, campaign_id: str) -> dict[str, tuple[str, str, dict[str, Any]]]:
    ledger = EventLedger(campaign)
    ledger.verify(campaign_id)
    result: dict[str, tuple[str, str, dict[str, Any]]] = {}
    for event in ledger.read_events(campaign_id):
        raw = ledger.cas.get(event.payload_ref)
        data = json.loads(raw)
        if not isinstance(data, dict) or canonical_json(data) != raw:
            raise PaperIntegrityError("Noncanonical campaign event payload")
        result[event.event_sha256] = (event.event_type, event.subject_id, data)
    return result


def _measured_table(
    claim_id: str,
    status: str,
    final: dict[str, Any],
    events: dict[str, tuple[str, str, dict[str, Any]]],
) -> dict[str, Any] | None:
    """Read only independently replayed terminal stdout-backed evaluator results."""
    plan = final.get("final_plan")
    result = final.get("final_result")
    if plan is None or result is None or result.get("status") != status:
        raise PaperIntegrityError("Verified final predicate lacks matching replay result")
    if status == "FINAL_INCONCLUSIVE":
        return None  # No complete measured comparison may be asserted.
    references = result["final_evidence_events"]
    if set(references) != {"baseline", plan["candidate_id"]}:
        raise PaperIntegrityError("Final comparison lacks two exact attempt references")
    arms: dict[str, dict[str, Any]] = {}
    for arm, event_hash in references.items():
        row = events.get(event_hash)
        if row is None or row[0] != "ATTEMPT_SUCCEEDED":
            raise PaperIntegrityError("Final comparison references a non-successful attempt")
        payload = row[2]
        result_data = payload.get("validated_result")
        stdout = payload.get("stdout_ref")
        if (
            not isinstance(result_data, dict)
            or not isinstance(stdout, dict)
            or result_data.get("metric") != plan["metric"]
            or result_data.get("direction") != plan["direction"]
            or result_data.get("samples_sha256") != plan["split_sha256"]
            or "sha256" not in stdout
        ):
            raise PaperIntegrityError("Replayed final arm does not match frozen metric identity")
        arms[arm] = {
            "metric_value": result_data["value"],
            "unit": result_data["unit"],
            "sample_count": result_data["sample_count"],
            "terminal_event_sha256": event_hash,
            "stdout_sha256": stdout["sha256"],
        }
    baseline = arms["baseline"]
    candidate = arms[plan["candidate_id"]]
    if (
        baseline["unit"] != candidate["unit"]
        or baseline["sample_count"] != candidate["sample_count"]
    ):
        raise PaperIntegrityError("Final arms have noncomparable units or sample counts")
    measured = (
        baseline["metric_value"] - candidate["metric_value"]
        if plan["direction"] == "min"
        else candidate["metric_value"] - baseline["metric_value"]
    )
    if measured != result["measured_improvement"]:
        raise PaperIntegrityError("Displayed metric contradicts replayed final improvement")
    return {
        "claim_id": claim_id,
        "status": status,
        "scope": "frozen final dataset only",
        "candidate_id": plan["candidate_id"],
        "baseline_commit": plan["baseline_commit"],
        "candidate_commit": plan["candidate_commit"],
        "split_sha256": plan["split_sha256"],
        "manifest_sha256": plan["manifest_sha256"],
        "policy_sha256": plan["policy_sha256"],
        "metric": plan["metric"],
        "direction": plan["direction"],
        "baseline": baseline,
        "candidate": candidate,
        "improvement": measured,
        "minimum_improvement": plan["threshold"],
        "selection_exposure": plan["qualification_attempt_exposure"],
        "selection_event_sha256": plan["selection_event_sha256"],
        "uncertainty": result["uncertainty"],
    }


def _bibliography(
    doi: str,
    subject_id: str | None,
    used: list[str],
    store: Path,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for archive_id in used:
        try:
            snapshot = read_snapshot(store, archive_id)
        except LiteratureArchiveError as exc:
            raise PaperIntegrityError(
                "Observed bibliography evidence changed during export"
            ) from exc
        for batch in snapshot["batches"]:
            for item in batch["items"]:
                if item["doi"] != doi or (
                    subject_id is not None and item["provider_id"] != subject_id
                ):
                    continue
                rows.append(
                    {
                        "doi": item["doi"],
                        "title": item["title"],
                        "authors": item["authors"],
                        "year": item["year"],
                        "provider": item["provider"],
                        "provider_id": item["provider_id"],
                        "source_url": item["source_url"],
                        "observed_at_utc": batch["observed_at_utc"],
                        "retrieval_completeness": batch["completeness"],
                        "snapshot_sha256": archive_id,
                        "record_sha256": item["raw_sha256"],
                    }
                )
    unique = {
        (row["snapshot_sha256"], row["record_sha256"], row["provider_id"]): row for row in rows
    }
    return [unique[key] for key in sorted(unique)]


def build_paper(
    spec: PaperSpec,
    *,
    campaign: Path | None = None,
    literature_store: Path | None = None,
) -> dict[str, Any]:
    """No evaluator, model or network; derive all evidence from existing verified stores."""
    audit = audit_claims(spec.claims, campaign=campaign, literature_store=literature_store)
    final: dict[str, Any] | None = None
    events: dict[str, tuple[str, str, dict[str, Any]]] = {}
    if campaign is not None:
        final = FinalService(campaign).inspect()
        events = _event_payloads(campaign, str(final["campaign_id"]))
    classifications = {
        "final_status": "FINAL_PREDICATE_ONLY",
        "qualification_status": "EXPLORATORY_SELECTION_ONLY",
        "literature_doi": "SOURCE_METADATA_ONLY",
    }
    claim_rows: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    bibliography: list[dict[str, Any]] = []
    for submitted, checked in zip(spec.claims.claims, audit["claims"], strict=True):
        kind = submitted.kind
        status = checked["status"]
        if kind == "final_status" and status == "VERIFIED":
            assert final is not None
            measurement = _measured_table(submitted.claim_id, submitted.expected, final, events)
            if measurement is not None:
                tables.append(measurement)
        if kind == "literature_doi" and status == "OBSERVED":
            if literature_store is None:
                raise PaperIntegrityError("Observed literature claim missing archive")
            bibliography.extend(
                _bibliography(
                    submitted.expected,
                    submitted.subject_id,
                    checked["used_evidence"],
                    literature_store,
                )
            )
        claim_rows.append(
            {
                **checked,
                "assertion_class": classifications[kind],
                "publication_use": (
                    "VERIFIED_BOUNDED_FINAL_PREDICATE"
                    if kind == "final_status" and status == "VERIFIED"
                    else "EXPLORATORY_ONLY"
                    if kind == "qualification_status" and status == "OBSERVED"
                    else "BIBLIOGRAPHIC_OBSERVATION_ONLY"
                    if kind == "literature_doi" and status == "OBSERVED"
                    else "NOT_SUPPORTED_FOR_FINDINGS"
                ),
                "wording_reviewed": False,
            }
        )
    dedup = {(r["snapshot_sha256"], r["record_sha256"], r["provider_id"]): r for r in bibliography}
    bibliography = [dedup[key] for key in sorted(dedup)]
    doc = {
        "schema_version": "1.0",
        "generator": "PaperForge deterministic evidence export v1",
        "title": spec.title,
        "authors": spec.authors,
        "campaign_id": audit["campaign_id"],
        "ledger_chain_head": final["ledger"]["chain_head"] if final is not None else None,
        "claims": claim_rows,
        "measurements": tables,
        "bibliography": bibliography,
        "audit_counts": audit["counts"],
        "limitations": [
            "Automated export is not peer review or evidence of novelty or publication readiness",
            "Submitted title, authors and claim wording are not semantically verified",
            "VERIFIED denotes a mechanical frozen-final predicate, not population superiority",
            "OBSERVED qualification is exploratory selection, not independent final evidence",
            "OBSERVED literature entries are source metadata, not verified scientific conclusions",
            "Missing, unknown, contradicted and unverifiable claims are not findings",
            "Aggregate evaluator data do not justify an invented confidence interval or p-value",
            "Local subprocesses are not a hostile-code security sandbox",
            "Local hash integrity is not tamper-proof against an administrator",
        ],
    }
    doc["content_sha256"] = digest_bytes(canonical_json(doc))
    return doc


def _safe(value: Any) -> str:
    """Escape untrusted provider, author and claim strings in generated Markdown."""
    value = str(value) if value is not None else "not supplied"
    value = re.sub(r"[\x00-\x1f\x7f]", " ", value)
    value = html.escape(value, quote=False)
    for token in ("\\", "|", "`", "[", "]", "*", "_", "!", "#", "<", ">"):
        value = value.replace(token, "\\" + token)
    return value


def render_markdown(paper: dict[str, Any]) -> str:
    rows = [
        "# " + _safe(paper["title"]),
        "",
        "**Machine-generated evidence inventory; not a publication-ready manuscript.**",
        "",
        "Authors (operator-supplied): "
        + (", ".join(_safe(name) for name in paper["authors"]) or "not supplied"),
        "",
        "## Methods and provenance",
        "",
        "ResearchForge's offline ResearchAuditor replayed supplied exact evidence hashes. "
        "Final measurements below are included only for independently replayed final predicates. "
        "The operator's free-text claims were not checked for semantic accuracy.",
        "",
        "Campaign: " + _safe(paper["campaign_id"]),
        "",
        "Ledger head: `" + _safe(paper["ledger_chain_head"]) + "`",
        "",
        "Document digest: `" + _safe(paper["content_sha256"]) + "`",
        "",
        "## Final-evaluation results (frozen dataset only)",
        "",
    ]
    if not paper["measurements"]:
        rows.extend(["**MISSING / INCOMPLETE:** No fully verified final measurement table.", ""])
    for item in paper["measurements"]:
        base, candidate = item["baseline"], item["candidate"]
        rows.extend(
            [
                "### " + _safe(item["claim_id"]),
                "",
                "| Metric | Baseline | Candidate | Improvement |",
                "| --- | ---: | ---: | ---: |",
                "| "
                + _safe(item["metric"])
                + " ("
                + _safe(base["unit"])
                + ") | "
                + _safe(base["metric_value"])
                + " | "
                + _safe(candidate["metric_value"])
                + " | "
                + _safe(item["improvement"])
                + " |",
                "",
                "Predeclared threshold: " + _safe(item["minimum_improvement"]) + "; "
                "direction: "
                + _safe(item["direction"])
                + "; cases: "
                + _safe(base["sample_count"])
                + "; status: **"
                + _safe(item["status"])
                + "**.",
                "",
                "Baseline commit: `" + _safe(item["baseline_commit"]) + "`",
                "",
                "Candidate commit: `" + _safe(item["candidate_commit"]) + "`",
                "",
                "Frozen split SHA-256: `" + _safe(item["split_sha256"]) + "`",
                "",
                "Frozen manifest SHA-256: `" + _safe(item["manifest_sha256"]) + "`",
                "",
                "Selection exposure: " + _safe(item["selection_exposure"]),
                "",
                "Baseline event/stdout: `"
                + _safe(base["terminal_event_sha256"])
                + "` / `"
                + _safe(base["stdout_sha256"])
                + "`",
                "",
                "Candidate event/stdout: `"
                + _safe(candidate["terminal_event_sha256"])
                + "` / `"
                + _safe(candidate["stdout_sha256"])
                + "`",
                "",
                "Uncertainty: " + _safe(item["uncertainty"]) + ".",
                "",
            ]
        )
    rows.extend(["## Submitted claims and missing evidence", ""])
    for item in paper["claims"]:
        rows.extend(
            [
                "### " + _safe(item["claim_id"]) + " — " + _safe(item["status"]),
                "",
                "Operator-supplied wording (NOT semantically verified): " + _safe(item["text"]),
                "",
                "Scope: " + _safe(item["scope"]),
                "",
                "Use: **" + _safe(item["publication_use"]) + "**",
                "",
                "Linked evidence: "
                + (", ".join("`" + _safe(x) + "`" for x in item["used_evidence"]) or "none"),
                "",
                "Missing evidence: "
                + (", ".join(_safe(x) for x in item["missing_evidence"]) or "none"),
                "",
                "Conflicts: " + (", ".join(_safe(x) for x in item["conflicts"]) or "none"),
                "",
                "Interpretation: " + _safe(item["note"]),
                "",
            ]
        )
    rows.extend(["## Source-specific bibliographic observations", ""])
    if not paper["bibliography"]:
        rows.extend(["**MISSING:** No verified source snapshot entries supplied.", ""])
    for entry in paper["bibliography"]:
        rows.extend(
            [
                "- "
                + _safe(entry["title"])
                + " ("
                + _safe(entry["year"])
                + "); "
                + ", ".join(_safe(n) for n in entry["authors"])
                + "; DOI: "
                + _safe(entry["doi"])
                + "; source: "
                + _safe(entry["provider"])
                + ":"
                + _safe(entry["provider_id"]),
                "  - Provider URL: "
                + _safe(entry["source_url"])
                + "; observed at: "
                + _safe(entry["observed_at_utc"])
                + "; completeness: "
                + _safe(entry["retrieval_completeness"]),
                "  - Snapshot SHA-256: `"
                + _safe(entry["snapshot_sha256"])
                + "`; record SHA-256: `"
                + _safe(entry["record_sha256"])
                + "`",
            ]
        )
    rows.extend(["", "## Limitations and disclosure", ""])
    rows.extend("- " + _safe(message) for message in paper["limitations"])
    return "\n".join(rows) + "\n"

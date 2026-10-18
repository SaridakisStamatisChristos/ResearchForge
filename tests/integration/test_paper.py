"""Evidence-closed PaperForge contract, offline demo, bibliography and CLI tests."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from researchforge.application.evidence import EventLedger
from researchforge.cli import app
from researchforge.demo import run_demo
from researchforge.domain.serialization import canonical_json, digest_bytes
from researchforge.literature import LiteratureRecord, RetrievalBatch, RetrievalPage
from researchforge.literature_archive import store_snapshot
from researchforge.paper import (
    PaperInputError,
    PaperSpec,
    build_paper,
    read_paper_spec,
    render_markdown,
)


def _claim(
    claim_id: str, kind: str, expected: str, sha: str | None,
    *, subject_id: str | None = None, text: str = "Measured, bounded research predicate"
) -> dict[str, Any]:
    return {
        "claim_id": claim_id,
        "kind": kind,
        "text": text,
        "scope": "Fixed local toy dataset",
        "expected": expected,
        "subject_id": subject_id,
        "evidence": (
            [{"source": "literature_snapshot" if kind == "literature_doi" else "campaign_event",
              "sha256": sha}]
            if sha is not None else []
        ),
    }


def _spec(claims: list[dict[str, Any]], title: str = "Toy research report") -> PaperSpec:
    return PaperSpec.model_validate({
        "schema_version": "1.0",
        "title": title,
        "authors": ["Example Researcher"],
        "claims": {"schema_version": "1.0", "claims": claims},
    })


def _fixture(tmp_path: Path) -> tuple[Path, str, str]:
    result = run_demo(tmp_path / "demo")
    root = Path(result["campaign_path"])
    ledger = EventLedger(root)
    events = ledger.read_events("toy-optimizer-v1")
    final = next(x.event_sha256 for x in events if x.event_type == "FINAL_EVALUATED")
    qualified = next(
        x.event_sha256 for x in reversed(events)
        if x.event_type == "QUALIFICATION_DECIDED" and x.subject_id == "good"
    )
    return root, final, qualified


def test_real_executed_final_produces_bounded_traceable_table(tmp_path: Path) -> None:
    campaign, final, qualified = _fixture(tmp_path)
    spec = _spec([
        _claim("final", "final_status", "FINAL_SUPPORTED", final,
               text="This unreviewed wording claims universal superiority"),
        _claim("selection", "qualification_status", "QUALIFIED", qualified, subject_id="good"),
        _claim("wrong", "final_status", "FINAL_NOT_SUPPORTED", final),
        _claim("unbacked", "final_status", "FINAL_SUPPORTED", None),
    ])
    report = build_paper(spec, campaign=campaign)
    assert report["audit_counts"]["VERIFIED"] == 1
    assert [c["status"] for c in report["claims"]] == [
        "VERIFIED", "OBSERVED", "CONTRADICTED", "INCONCLUSIVE"
    ]
    assert [c["publication_use"] for c in report["claims"]] == [
        "VERIFIED_BOUNDED_FINAL_PREDICATE", "EXPLORATORY_ONLY",
        "NOT_SUPPORTED_FOR_FINDINGS", "NOT_SUPPORTED_FOR_FINDINGS"
    ]
    assert len(report["measurements"]) == 1
    row = report["measurements"][0]
    assert row["scope"] == "frozen final dataset only"
    assert row["split_sha256"] == json.loads(
        (campaign / "manifest.lock.json").read_text()
    )["splits"]["final"]["sha256"]
    assert row["baseline"]["stdout_sha256"] and row["candidate"]["stdout_sha256"]
    assert row["improvement"] == (
        row["baseline"]["metric_value"] - row["candidate"]["metric_value"]
    )
    assert row["selection_exposure"] >= 1
    assert report["content_sha256"] == build_paper(spec, campaign=campaign)["content_sha256"]
    md = render_markdown(report)
    assert "NOT semantically verified" in md
    assert "CONTRADICTED" in md and "MISSING" in md
    assert "Aggregate evaluator data" in md
    assert "cannot" not in row["uncertainty"].lower()


def test_missing_and_foreign_evidence_never_produce_metric_table(tmp_path: Path) -> None:
    campaign, _, _ = _fixture(tmp_path)
    spec = _spec([
        _claim("foreign", "final_status", "FINAL_SUPPORTED", "0" * 64),
        _claim("no-link", "qualification_status", "QUALIFIED", None, subject_id="good"),
    ])
    report = build_paper(spec, campaign=campaign)
    assert report["measurements"] == []
    assert report["bibliography"] == []
    assert [c["status"] for c in report["claims"]] == ["UNVERIFIED", "INCONCLUSIVE"]
    assert "No fully verified final measurement table" in render_markdown(report)


def test_source_specific_bibliography_is_only_observed_metadata(tmp_path: Path) -> None:
    raw: dict[str, object] = {"DOI": "10.1234/paper", "title": ["<unsafe> * injected"]}
    entry = LiteratureRecord(
        provider="crossref", provider_id="10.1234/paper", kind="work",
        doi="10.1234/paper", title="<unsafe> * injected", authors=["A & B"],
        year=2026, source_url=None, raw_sha256=digest_bytes(canonical_json(raw)), raw=raw
    )
    observed = datetime.now(UTC)
    page = RetrievalPage(
        requested_url="https://api.crossref.org/works", parameters={},
        observed_at_utc=observed, http_status=200,
        response_sha256=digest_bytes(b"response"), result_count=1, retry_count=0,
        rate_limit_retry_after=None, failure_reason=None,
    )
    batch = RetrievalBatch(
        provider="crossref", kind="works", query="paper", items=[entry],
        pages=[page], requested_url=page.requested_url,
        parameters={}, observed_at_utc=observed,
        response_hash=digest_bytes(b"response"), reported_total=1,
        retrieved_total=1, next_cursor=None, completeness="PARTIAL",
        failure_reason="page cap", lookup_status="FOUND",
    )
    store = tmp_path / "store"
    sid = store_snapshot(store, [batch])
    spec = _spec([
        _claim("paper", "literature_doi", "10.1234/paper", sid),
        _claim("absent", "literature_doi", "10.1234/missing", sid),
    ])
    doc = build_paper(spec, literature_store=store)
    assert len(doc["bibliography"]) == 1
    assert doc["bibliography"][0]["retrieval_completeness"] == "PARTIAL"
    assert doc["bibliography"][0]["record_sha256"] == entry.raw_sha256
    assert [c["status"] for c in doc["claims"]] == ["OBSERVED", "INCONCLUSIVE"]
    assert doc["measurements"] == []
    rendered = render_markdown(doc)
    assert "&lt;unsafe&gt;" in rendered
    assert r"\*" in rendered
    assert "BIBLIOGRAPHIC_OBSERVATION_ONLY" in rendered
    (store / f"{sid}.json").write_bytes(b"corrupt")
    tampered = build_paper(spec, literature_store=store)
    assert tampered["bibliography"] == []
    assert tampered["claims"][0]["status"] == "UNVERIFIED"


def test_cli_no_clobber_and_integrity_fail_closed(tmp_path: Path) -> None:
    campaign, final, _ = _fixture(tmp_path)
    spec = _spec([_claim("final", "final_status", "FINAL_SUPPORTED", final)])
    input_file = tmp_path / "paper.json"
    input_file.write_bytes(canonical_json(spec.model_dump(mode="json")))
    output = tmp_path / "paper.md"
    cli = CliRunner()
    args = ["paper", "draft", "--input", str(input_file), "--campaign", str(campaign)]
    good = cli.invoke(app, args + ["--format", "md", "--output", str(output)])
    assert good.exit_code == 0, good.output
    assert "Final-evaluation results" in output.read_text()
    again = cli.invoke(app, args + ["--format", "md", "--output", str(output)])
    assert again.exit_code == 2
    assert "Final-evaluation results" in output.read_text()
    as_json = cli.invoke(app, args + ["--format", "json"])
    assert as_json.exit_code == 0, as_json.output
    assert len(json.loads(as_json.output)["measurements"]) == 1
    ledger = EventLedger(campaign)
    blob = ledger.cas.root
    blob_file = next(path for path in blob.rglob("*") if path.is_file())
    blob_file.write_bytes(b"corruption")
    failure = cli.invoke(app, args + ["--format", "json"])
    assert failure.exit_code == 6, failure.output


def test_spec_strict_validation_and_absent_sources(tmp_path: Path) -> None:
    source = tmp_path / "bad.json"
    source.write_text('{"schema_version":"1.0","schema_version":"1.0"}')
    with pytest.raises(PaperInputError):
        read_paper_spec(source)
    source.write_text('{"schema_version": NaN}')
    with pytest.raises(PaperInputError):
        read_paper_spec(source)
    with pytest.raises(ValidationError):
        _spec([_claim("x", "final_status", "FINAL_SUPPORTED", None)], title="\n")
    spec = _spec([_claim("x", "final_status", "FINAL_SUPPORTED", None)])
    result = build_paper(spec)
    assert result["claims"][0]["status"] == "NOT_EVALUATED"
    assert result["measurements"] == []

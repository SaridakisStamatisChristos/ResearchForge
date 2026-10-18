"""PR09 contracts: deterministic, fail-closed claim audit with real offline fixture."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from researchforge.audit import (
    ClaimBatch,
    audit_claims,
    markdown_report,
    read_claims,
)
from researchforge.cli import app
from researchforge.demo import run_demo
from researchforge.domain.serialization import canonical_json, digest_bytes
from researchforge.literature import LiteratureRecord, RetrievalBatch, RetrievalPage
from researchforge.literature_archive import store_snapshot


def _batch(items: list[dict[str, object]]) -> ClaimBatch:
    return ClaimBatch.model_validate({"schema_version": "1.0", "claims": items})


def _claim(
    cid: str,
    kind: str,
    expected: str,
    evidence: list[dict[str, str]],
    subject: str | None = None,
) -> dict[str, object]:
    return {
        "claim_id": cid,
        "kind": kind,
        "text": "A bounded measurable statement",
        "scope": "Only this fixture and frozen inputs",
        "expected": expected,
        "subject_id": subject,
        "evidence": evidence,
    }


def test_frozen_final_and_selection_events(tmp_path: Path) -> None:
    fixture = run_demo(tmp_path / "demo")
    root = Path(fixture["campaign_path"])
    from researchforge.application.evidence import EventLedger

    ledger = EventLedger(root)
    events = ledger.read_events("toy-optimizer-v1")
    final = next(event for event in events if event.event_type == "FINAL_EVALUATED")
    qualified = next(
        event
        for event in reversed(events)
        if event.event_type == "QUALIFICATION_DECIDED" and event.subject_id == "good"
    )

    def links(sha: str) -> list[dict[str, str]]:
        return [{"source": "campaign_event", "sha256": sha}]

    batch = _batch(
        [
            _claim("supported", "final_status", "FINAL_SUPPORTED", links(final.event_sha256)),
            _claim(
                "contradicted", "final_status", "FINAL_NOT_SUPPORTED", links(final.event_sha256)
            ),
            _claim(
                "selection",
                "qualification_status",
                "QUALIFIED",
                links(qualified.event_sha256),
                "good",
            ),
            _claim("missing", "final_status", "FINAL_SUPPORTED", []),
            _claim("foreign", "final_status", "FINAL_SUPPORTED", links("f" * 64)),
        ]
    )
    report = audit_claims(batch, campaign=root)
    assert [item["status"] for item in report["claims"]] == [
        "VERIFIED",
        "CONTRADICTED",
        "OBSERVED",
        "INCONCLUSIVE",
        "UNVERIFIED",
    ]
    assert report["claims"][1]["conflicts"] == ["actual=FINAL_SUPPORTED"]
    assert report["counts"]["VERIFIED"] == 1
    assert "not peer review" in markdown_report(report).lower()
    file = tmp_path / "claims.json"
    file.write_bytes(canonical_json(batch.model_dump(mode="json")))
    runner = CliRunner()
    result = runner.invoke(app, ["audit", "claims", "--input", str(file), "--campaign", str(root)])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["data"]["counts"]["CONTRADICTED"] == 1
    corrupt = root / ".researchforge" / "blobs"
    blob = next(entry for entry in corrupt.rglob("*") if entry.is_file())
    # A validated event-chain or source blob corruption must never give a clean audit.
    blob.write_bytes(b"changed")
    failure = runner.invoke(app, ["audit", "claims", "--input", str(file), "--campaign", str(root)])
    assert failure.exit_code == 6


def test_metadata_observed_never_becomes_scientific_support(tmp_path: Path) -> None:
    raw: dict[str, object] = {"DOI": "10.1234/example", "title": ["Example"]}
    record = LiteratureRecord(
        provider="crossref",
        provider_id="10.1234/example",
        kind="work",
        doi="10.1234/example",
        title="Example",
        authors=[],
        year=2026,
        source_url=None,
        raw_sha256=digest_bytes(canonical_json(raw)),
        raw=raw,
    )
    observed = datetime.now(UTC)
    page = RetrievalPage(
        requested_url="https://api.crossref.org/works",
        parameters={},
        observed_at_utc=observed,
        http_status=200,
        response_sha256=digest_bytes(b"observed"),
        result_count=1,
        retry_count=0,
        rate_limit_retry_after=None,
        failure_reason=None,
    )
    batch = RetrievalBatch(
        provider="crossref",
        kind="works",
        query="example",
        items=[record],
        pages=[page],
        requested_url=page.requested_url,
        parameters={},
        observed_at_utc=observed,
        response_hash=digest_bytes(b"observed"),
        reported_total=1,
        retrieved_total=1,
        next_cursor=None,
        completeness="COMPLETE",
        failure_reason=None,
        lookup_status="FOUND",
    )
    archive = store_snapshot(tmp_path / "archives", [batch])
    assertions = _batch(
        [
            _claim(
                "observed",
                "literature_doi",
                "10.1234/example",
                [{"source": "literature_snapshot", "sha256": archive}],
            ),
            _claim(
                "absent",
                "literature_doi",
                "10.1234/missing",
                [{"source": "literature_snapshot", "sha256": archive}],
            ),
        ]
    )
    result = audit_claims(assertions, literature_store=tmp_path / "archives")
    assert [item["status"] for item in result["claims"]] == ["OBSERVED", "INCONCLUSIVE"]
    (tmp_path / "archives" / f"{archive}.json").write_bytes(b"tampered")
    corrupt = audit_claims(assertions, literature_store=tmp_path / "archives")
    assert corrupt["claims"][0]["status"] == "UNVERIFIED"


def test_bad_and_missing_claims_fail_closed(tmp_path: Path) -> None:
    link = [{"source": "literature_snapshot", "sha256": "0" * 64}]
    with pytest.raises(ValidationError):
        _batch(
            [
                _claim("dup", "literature_doi", "10.1234/a", link),
                _claim("dup", "literature_doi", "10.1234/a", link),
            ]
        )
    with pytest.raises(ValidationError):
        _batch([_claim("wrong", "final_status", "FINAL_SUPPORTED", link)])
    for body in (
        b'{"schema_version":"1.0","claims":[],"claims":[]}',
        b'{"schema_version":"1.0","claims":[{"claim_id":NaN}]}',
    ):
        f = tmp_path / "bad.json"
        f.write_bytes(body)
        with pytest.raises(ValueError):
            read_claims(f)
    assertions = _batch([_claim("no-context", "final_status", "FINAL_SUPPORTED", [])])
    assert audit_claims(assertions)["claims"][0]["status"] == "NOT_EVALUATED"

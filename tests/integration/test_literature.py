"""PR08 provider contract tests: no live internet, all HTTP via deterministic mock transports."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from typer.testing import CliRunner

from researchforge.cli import app
from researchforge.literature import (
    CrossrefProvider,
    LiteratureInputError,
    LiteratureQuery,
    OpenAlexProvider,
    RetrievalBatch,
    group_by_doi,
    normalize_doi,
    parse_sources,
    run_lookup,
)
from researchforge.literature_archive import (
    LiteratureArchiveError,
    read_snapshot,
    report_markdown,
    store_snapshot,
)


def crossref_item(doi: str = "10.1234/EXAMPLE") -> dict[str, Any]:
    return {
        "DOI": doi,
        "title": ["Crossref's own title"],
        "author": [{"given": "Alex", "family": "Smith"}],
        "published": {"date-parts": [[2024, 3, 1]]},
        "is-referenced-by-count": 13,
        "type": "journal-article",
    }


def openalex_item(doi: str = "https://doi.org/10.1234/example") -> dict[str, Any]:
    return {
        "id": "https://openalex.org/W123456",
        "doi": doi,
        "display_name": "OpenAlex's variant title",
        "publication_year": 2023,
        "authorships": [{"author": {"display_name": "Alex S."}}],
        "cited_by_count": 27,
    }


def response(body: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body)


def cr_response(items: list[dict[str, Any]], total: int) -> httpx.Response:
    return response({"status": "ok", "message": {"items": items, "total-results": total}})


def oa_response(items: list[dict[str, Any]], total: int, nxt: str | None) -> httpx.Response:
    return response({"results": items, "meta": {"count": total, "next_cursor": nxt}})


def invoke(handler: Any, operation: str = "lookup", **opts: Any) -> RetrievalBatch:
    async def execute() -> RetrievalBatch:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider: Any = (
                OpenAlexProvider(client=client) if opts.pop("provider", "crossref") == "openalex"
                else CrossrefProvider(client=client)
            )
            if operation == "lookup":
                return await provider.lookup_identifier("10.1234/example")
            return await provider.search(
                LiteratureQuery(text="dynamic shortest paths", **opts)
            )
    return asyncio.run(execute())


@pytest.mark.parametrize(
    "source,expected",
    [
        ("10.1234/ABC-123", "10.1234/abc-123"),
        (" DOI:10.1234/AbC-123 ", "10.1234/abc-123"),
        ("https://doi.org/10.1234/ABC-123", "10.1234/abc-123"),
        ("http://dx.doi.org/10.1234/AbC", "10.1234/abc"),
        ("https://doi.org/10.1234/abc%2Fdef", "10.1234/abc/def"),
    ],
)
def test_normalize_doi(source: str, expected: str) -> None:
    assert normalize_doi(source) == expected


@pytest.mark.parametrize(
    "bad",
    [
        "https://example.net/10.1234/abc",
        "https://doi.org/10.1234/x?token=secret",
        "10.12/short",
        "10.1234/",
        "https://doi.org/10.1234/a#fragment",
        "10.1234/a b",
        "https://user:secret@doi.org/10.1234/a",
        "../10.1234/example",
        "10.1234/a%0Ab",
    ],
)
def test_invalid_doi_rejected(bad: str) -> None:
    with pytest.raises(LiteratureInputError):
        normalize_doi(bad)


def test_both_sources_exact_lookup_preserves_conflicting_metadata() -> None:
    seen: list[str] = []

    def crossref(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.crossref.org"
        seen.append("crossref")
        return response({"status": "ok", "message": crossref_item()})

    def openalex(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.openalex.org"
        seen.append("openalex")
        return response(openalex_item())

    async def read() -> list[RetrievalBatch]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(crossref)) as cr:
            async with httpx.AsyncClient(transport=httpx.MockTransport(openalex)) as oa:
                return await run_lookup(
                    "DOI:10.1234/EXAMPLE",
                    sources=("crossref", "openalex"),
                    clients={"crossref": cr, "openalex": oa},
                )

    batches = asyncio.run(read())
    assert seen == ["crossref", "openalex"]
    assert all(b.completeness == "COMPLETE" and b.lookup_status == "FOUND" for b in batches)
    assert all(b.retrieved_total == 1 and b.response_hash for b in batches)
    assert batches[0].items[0].doi == batches[1].items[0].doi
    assert batches[0].items[0].title != batches[1].items[0].title
    assert batches[0].items[0].raw["is-referenced-by-count"] == 13
    assert batches[1].items[0].raw["cited_by_count"] == 27
    groups = group_by_doi(batches)
    assert len(groups["doi:10.1234/example"]) == 2
    assert groups["doi:10.1234/example"][0].provider != groups["doi:10.1234/example"][1].provider


def test_crossref_negative_lookup_means_source_only() -> None:
    batch = invoke(lambda _: httpx.Response(404))
    assert batch.completeness == "COMPLETE" and batch.lookup_status == "NOT_FOUND"
    assert batch.retrieved_total == 0 and batch.reported_total == 0


def test_http_200_provider_sentinel_is_not_success() -> None:
    batch = invoke(lambda _: response({"status": "error", "message": {}}))
    assert batch.completeness == "UNKNOWN"
    assert batch.failure_reason == "INVALID_WORK"
    assert batch.retrieved_total == 0


def test_crossref_returned_doi_mismatch_is_unknown() -> None:
    batch = invoke(lambda _: response({"status": "ok", "message": crossref_item("10.9876/other")}))
    assert batch.completeness == "UNKNOWN" and batch.failure_reason == "INVALID_WORK"


def test_openalex_author_search_separate_from_work_identity() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/authors")
        return oa_response(
            [{"id": "https://openalex.org/A42", "display_name": "Ada Science"}], 1, None
        )

    batch = invoke(handler, "search", provider="openalex", kind="authors", limit=5)
    assert batch.completeness == "COMPLETE"
    assert batch.items[0].kind == "author"
    assert batch.items[0].doi is None
    assert batch.items[0].title == "Ada Science"


def test_openalex_cursor_pagination_and_limit_is_partial() -> None:
    cursors: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cursor = request.url.params["cursor"]
        cursors.append(cursor)
        item = dict(openalex_item())
        item["id"] += cursor
        if cursor == "*":
            return oa_response([item], 4, "cursor-two")
        assert cursor == "cursor-two"
        return oa_response([item], 4, "cursor-three")

    batch = invoke(
        handler, "search", provider="openalex", page_size=1, limit=2, max_pages=2
    )
    assert batch.completeness == "PARTIAL"
    assert batch.retrieved_total == 2
    assert batch.next_cursor == "cursor-three"
    assert cursors == ["*", "cursor-two"]
    assert len(batch.pages) == 2
    assert all(page.response_sha256 for page in batch.pages)


def test_openalex_repeated_cursor_fails_closed() -> None:
    batch = invoke(
        lambda _: oa_response([openalex_item()], 9, "*"),
        "search", provider="openalex", page_size=1, max_pages=3,
    )
    assert batch.completeness == "PARTIAL"
    assert batch.failure_reason == "CURSOR_CYCLE"
    assert batch.retrieved_total == 1


def test_crossref_search_respects_page_and_total_limits() -> None:
    offsets: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        offsets.append(offset)
        return cr_response([crossref_item(f"10.1234/X{offset}")], total=3)

    batch = invoke(handler, "search", limit=2, page_size=1, max_pages=5)
    assert offsets == [0, 1]
    assert batch.completeness == "PARTIAL"
    assert batch.next_cursor == "2"
    assert batch.retrieved_total == 2 and batch.reported_total == 3


def test_successful_retry_records_rate_limit_and_does_not_drop_items() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return response({"status": "ok", "message": crossref_item()})

    batch = invoke(handler)
    assert calls == 2 and batch.completeness == "COMPLETE"
    assert batch.pages[0].retry_count == 1
    assert batch.pages[0].rate_limit_retry_after == "0"


def test_provider_503_after_valid_page_preserves_partial_evidence() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if request.url.params["offset"] == "0":
            return cr_response([crossref_item()], 4)
        return httpx.Response(503, text="unavailable")

    batch = invoke(handler, "search", page_size=1, max_pages=2)
    assert batch.retrieved_total == 1
    assert batch.completeness == "PARTIAL"
    assert batch.failure_reason == "HTTP_503"
    assert calls == 4  # first page, three bounded attempts on the failing second page


def test_provider_permanent_429_is_unknown_without_fabricated_success() -> None:
    batch = invoke(lambda _: httpx.Response(429, headers={"Retry-After": "0"}))
    assert batch.completeness == "UNKNOWN"
    assert batch.retrieved_total == 0
    assert batch.failure_reason == "HTTP_429"


def test_malformed_json_and_overlarge_body_fail_closed() -> None:
    malformed = invoke(lambda _: httpx.Response(200, content=b'{"status":"ok","status":"ok"}'))
    assert malformed.completeness == "UNKNOWN" and malformed.failure_reason == "MALFORMED_JSON"
    large = invoke(lambda _: httpx.Response(200, content=b"x" * 1_000_001))
    assert large.completeness == "UNKNOWN" and large.failure_reason == "BODY_TOO_LARGE"


def test_malformed_openalex_page_is_partial_if_prior_page_valid() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.params["cursor"] == "*":
            return oa_response([openalex_item()], 3, "later")
        return response({"results": "not-a-list", "meta": {"count": 3}})

    batch = invoke(handler, "search", provider="openalex", page_size=1, limit=3)
    assert batch.completeness == "PARTIAL"
    assert batch.retrieved_total == 1
    assert batch.failure_reason == "INVALID_PROVIDER_PAYLOAD"


def test_network_is_disabled_by_default_and_unknown_source_refused() -> None:
    with pytest.raises(LiteratureInputError, match="allow-network"):
        asyncio.run(CrossrefProvider().lookup_identifier("10.1234/example"))
    assert parse_sources("crossref,openalex") == ("crossref", "openalex")
    with pytest.raises(LiteratureInputError):
        parse_sources("crossref,crossref")
    with pytest.raises(LiteratureInputError):
        parse_sources("openalex,unknown")
    with pytest.raises(LiteratureInputError):
        asyncio.run(CrossrefProvider().search(LiteratureQuery(text="x", kind="authors")))
    cli = CliRunner()
    assert cli.invoke(app, ["literature", "lookup", "--doi", "10.1234/x"]).exit_code == 2
    assert cli.invoke(app, ["literature", "search", "--query", "graph"]).exit_code == 2


def test_archive_hash_replay_and_json_markdown_report(tmp_path: Path) -> None:
    batch = invoke(lambda _: response({"status": "ok", "message": crossref_item()}))
    folder = tmp_path / "literature"
    ident = store_snapshot(folder, [batch])
    assert len(ident) == 64
    assert store_snapshot(folder, [batch]) == ident
    data = read_snapshot(folder, ident)
    assert "doi:10.1234/example" in data["canonical_groups"]
    assert "Crossref's own title" in report_markdown(data)
    cli = CliRunner()
    report = cli.invoke(app, [
        "literature", "report", "--search-id", ident, "--store", str(folder),
        "--format", "json",
    ])
    assert report.exit_code == 0, report.output
    assert json.loads(report.output)["canonical_groups"] == data["canonical_groups"]
    exported = tmp_path / "report.md"
    assert cli.invoke(app, [
        "literature", "report", "--search-id", ident, "--store", str(folder),
        "--output", str(exported),
    ]).exit_code == 0
    assert exported.read_text() == report_markdown(data)
    assert cli.invoke(app, [
        "literature", "report", "--search-id", ident, "--store", str(folder),
        "--output", str(exported),
    ]).exit_code == 2


def test_archive_fails_tamper_detection_and_path_escape(tmp_path: Path) -> None:
    batch = invoke(lambda _: response({"status": "ok", "message": crossref_item()}))
    folder = tmp_path / "snapshots"
    ident = store_snapshot(folder, [batch])
    with pytest.raises(LiteratureArchiveError):
        read_snapshot(folder, "../bad")
    (folder / f"{ident}.json").write_bytes(b'{"forged":"success"}')
    with pytest.raises(LiteratureArchiveError):
        read_snapshot(folder, ident)
    with pytest.raises(LiteratureArchiveError):
        store_snapshot(folder, [batch])


def test_no_doi_does_not_merge_title_similarities() -> None:
    def first(request: httpx.Request) -> httpx.Response:
        return oa_response([
            {"id": "https://openalex.org/W1", "display_name": "Same title", "doi": None},
            {"id": "https://openalex.org/W2", "display_name": "Same title", "doi": None},
        ], 2, None)
    batch = invoke(first, "search", provider="openalex", limit=2)
    groups = group_by_doi([batch])
    assert len(groups) == 2


def test_mocked_cli_lookup_snapshot(tmp_path: Path) -> None:
    from researchforge import cli

    batch = invoke(lambda _: response({"status": "ok", "message": crossref_item()}))

    async def fake(*args: Any, **kwargs: Any) -> list[RetrievalBatch]:
        return [batch]

    with patch.object(cli, "run_lookup", side_effect=fake):
        result = CliRunner().invoke(app, [
            "literature", "lookup", "--doi", "10.1234/example",
            "--sources", "crossref", "--allow-network", "--json",
            "--store", str(tmp_path),
        ])
    assert result.exit_code == 0, result.output
    result_json = json.loads(result.output)
    assert result_json["status"] == "OK"
    assert read_snapshot(tmp_path, result_json["data"]["search_id"])["batches"]

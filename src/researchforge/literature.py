"""LiteratureForge v0.1: independent, bounded scholarly-source retrieval.

This module is not connected to experiment or final-evaluation state. A source
hit is a provider observation, not verification of a claim or global discovery.
Network use is opt-in; tests inject an httpx.MockTransport client.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast
from urllib.parse import quote, unquote, urlsplit

from pydantic import Field, ValidationError

from researchforge.domain.models import StrictModel
from researchforge.domain.serialization import canonical_json, digest_bytes

ProviderName = Literal["openalex", "crossref"]
Completeness = Literal["COMPLETE", "PARTIAL", "UNKNOWN"]
Kind = Literal["works", "authors"]
MAX_PAGES = 5
MAX_LIMIT = 100
MAX_BODY = 1_000_000
RETRY_CODES = {429, 500, 502, 503, 504}
_DOI = re.compile(r"^10\.\d{4,9}/[^\s?#<>]+$", re.IGNORECASE)
_HEX = re.compile(r"^[a-f0-9]{64}$")


class LiteratureInputError(ValueError):
    """Invalid DOI/query or attempted network access without opt-in."""


class LiteratureSourceError(Exception):
    """A provider response cannot be treated as successfully retrieved evidence."""


def normalize_doi(value: str) -> str:
    """Normalize identifiers without guessing matches or accepting DOI URLs with queries."""
    if not isinstance(value, str):
        raise LiteratureInputError("DOI must be text")
    raw = value.strip()
    if raw.lower().startswith("doi:"):
        raw = raw[4:].strip()
    if raw.lower().startswith(("https://", "http://")):
        parts = urlsplit(raw)
        if (
            parts.hostname is None
            or parts.hostname.lower() not in {"doi.org", "dx.doi.org"}
            or parts.username is not None or parts.password is not None
            or parts.query or parts.fragment or parts.port is not None
        ):
            raise LiteratureInputError("Only a bare DOI or doi.org resolver URL is allowed")
        raw = parts.path.lstrip("/")
    raw = unquote(raw)
    if not _DOI.fullmatch(raw) or len(raw) > 512 or any(ord(x) < 33 for x in raw):
        raise LiteratureInputError("Invalid DOI syntax")
    return raw.casefold()


class LiteratureQuery(StrictModel):
    text: str = Field(min_length=1, max_length=300)
    kind: Kind = "works"
    limit: int = Field(default=20, ge=1, le=MAX_LIMIT)
    page_size: int = Field(default=20, ge=1, le=50)
    max_pages: int = Field(default=MAX_PAGES, ge=1, le=MAX_PAGES)


class LiteratureRecord(StrictModel):
    provider: ProviderName
    provider_id: str = Field(min_length=1)
    kind: Literal["work", "author"]
    doi: str | None
    title: str | None
    authors: list[str] = Field(default_factory=list)
    year: int | None
    source_url: str | None
    raw_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    raw: dict[str, Any]


class RetrievalPage(StrictModel):
    requested_url: str
    parameters: dict[str, str | int]
    observed_at_utc: datetime
    http_status: int | None
    response_sha256: str | None
    result_count: int
    retry_count: int
    rate_limit_retry_after: str | None
    failure_reason: str | None


class RetrievalBatch(StrictModel):
    provider: ProviderName
    kind: Kind
    query: str
    items: list[LiteratureRecord]
    pages: list[RetrievalPage]
    requested_url: str
    parameters: dict[str, str | int]
    observed_at_utc: datetime
    response_hash: str | None
    reported_total: int | None
    retrieved_total: int
    next_cursor: str | None
    completeness: Completeness
    failure_reason: str | None
    lookup_status: Literal["FOUND", "NOT_FOUND", "NOT_APPLICABLE"]


class LiteratureProvider(Protocol):
    async def lookup_identifier(self, identifier: str) -> RetrievalBatch: ...

    async def search(self, query: LiteratureQuery) -> RetrievalBatch: ...


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("Duplicate provider JSON key")
        out[key] = value
    return out


def _constant(value: str) -> None:
    raise ValueError(f"Invalid provider numeric constant: {value}")


def _first_title(obj: Any) -> str | None:
    if isinstance(obj, str):
        return obj.strip() or None
    if isinstance(obj, list) and obj and isinstance(obj[0], str):
        return obj[0].strip() or None
    return None


def _year(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 1400 <= value <= 2200:
        return None
    return value


def _provider_doi(raw: Any) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return normalize_doi(raw)
    except LiteratureInputError:
        return None


def _normalize_item(provider: ProviderName, item: dict[str, Any], kind: Kind) -> LiteratureRecord:
    if provider == "openalex":
        pid = item.get("id")
        title = _first_title(item.get("display_name"))
        if kind == "works":
            author_rows = item.get("authorships", [])
            authors = [
                name for row in author_rows if isinstance(row, dict)
                for name in [_first_title(row.get("author", {}).get("display_name"))
                             if isinstance(row.get("author"), dict) else None]
                if name is not None
            ] if isinstance(author_rows, list) else []
            doi = _provider_doi(item.get("doi"))
            year = _year(item.get("publication_year"))
        else:
            authors, doi, year = [], None, None
    else:
        if kind != "works":
            raise LiteratureSourceError("Crossref author search is not an API contract")
        pid = item.get("DOI")
        title = _first_title(item.get("title"))
        raw_authors = item.get("author", [])
        authors = []
        if isinstance(raw_authors, list):
            for entry in raw_authors:
                if isinstance(entry, dict):
                    name = " ".join(
                        part.strip() for part in (entry.get("given"), entry.get("family"))
                        if isinstance(part, str) and part.strip()
                    )
                    if name:
                        authors.append(name)
        doi = _provider_doi(item.get("DOI"))
        date_info = item.get("published", item.get("issued", {}))
        parts = date_info.get("date-parts", []) if isinstance(date_info, dict) else []
        year = _year(parts[0][0]) if (
            isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]
        ) else None
    if not isinstance(pid, str) or not pid.strip():
        raise LiteratureSourceError("Provider record has no stable provider ID")
    raw = dict(item)
    return LiteratureRecord(
        provider=provider,
        provider_id=pid,
        kind="work" if kind == "works" else "author",
        doi=doi,
        title=title,
        authors=authors,
        year=year,
        source_url=pid if pid.startswith("https://") else None,
        raw_sha256=digest_bytes(canonical_json(raw)),
        raw=raw,
    )


class _PageResult:
    def __init__(self, page: RetrievalPage, body: dict[str, Any] | None, not_found: bool):
        self.page, self.body, self.not_found = page, body, not_found


class HttpSource:
    """No caller URL, redirect, proxy inheritance or credential is accepted by default."""

    def __init__(
        self,
        provider: ProviderName,
        *,
        allow_network: bool = False,
        client: Any = None,
    ) -> None:
        self.provider = provider
        self.allow_network = allow_network
        self._client = client

    async def _fetch(
        self, url: str, parameters: dict[str, str | int]
    ) -> _PageResult:
        if self._client is None and not self.allow_network:
            raise LiteratureInputError("Live literature access requires --allow-network")
        host = urlsplit(url).hostname
        expected_host = "api.openalex.org" if self.provider == "openalex" else "api.crossref.org"
        if host != expected_host or not url.startswith("https://"):
            raise LiteratureInputError("Provider URL outside fixed allowlist")
        observed = datetime.now(UTC)
        response: Any = None
        error: str | None = None
        retries = 0
        retry_after: str | None = None
        client = self._client
        owned = client is None
        if owned:
            try:
                import httpx
            except ImportError as exc:
                raise LiteratureInputError(
                    "Install ResearchForge with the [literature] extra for live HTTP"
                ) from exc
            client = httpx.AsyncClient(
                timeout=httpx.Timeout(8.0), follow_redirects=False,
                trust_env=False, headers={"User-Agent": "ResearchForge/0.1 (research metadata client)"},
            )
        try:
            for attempt in range(3):
                try:
                    response = await client.get(url, params=parameters)
                except Exception as exc:
                    # A bounded request never silently becomes a fabricated empty success.
                    error = f"NETWORK_{type(exc).__name__}"
                    if attempt < 2:
                        retries += 1
                        await asyncio.sleep(0.025 * (attempt + 1))
                        continue
                    break
                status = int(response.status_code)
                if status not in RETRY_CODES:
                    break
                error = f"HTTP_{status}"
                retry_after = response.headers.get("Retry-After")
                if attempt < 2:
                    retries += 1
                    pause = 0.05 * (attempt + 1) + random.random() * 0.025
                    if retry_after is not None and retry_after.isascii() and retry_after.isdigit():
                        pause = min(2.0, max(pause, float(retry_after)))
                    await asyncio.sleep(pause)
        finally:
            if owned:
                await client.aclose()
        request_url = url
        if response is not None:
            # The request URL contains only controlled source endpoints and search parameters.
            request_url = str(response.request.url)
        status_value: int | None = int(response.status_code) if response is not None else None
        if status_value == 404:
            return _PageResult(
                RetrievalPage(
                    requested_url=request_url, parameters=parameters, observed_at_utc=observed,
                    http_status=404, response_sha256=digest_bytes(response.content),
                    result_count=0, retry_count=retries,
                    rate_limit_retry_after=retry_after, failure_reason=None,
                ), None, True
            )
        body: dict[str, Any] | None = None
        raw: bytes | None = None
        if response is not None and status_value == 200:
            raw = bytes(response.content)
            if len(raw) > MAX_BODY:
                error = "BODY_TOO_LARGE"
            else:
                try:
                    obj = json.loads(
                        raw.decode("utf-8"), object_pairs_hook=_object_pairs,
                        parse_constant=_constant,
                    )
                    if not isinstance(obj, dict):
                        raise ValueError("Response is not an object")
                    body = obj
                    error = None
                except (ValueError, UnicodeError):
                    error = "MALFORMED_JSON"
        elif response is not None:
            error = f"HTTP_{status_value}"
        page = RetrievalPage(
            requested_url=request_url, parameters=parameters, observed_at_utc=observed,
            http_status=status_value, response_sha256=digest_bytes(raw) if raw is not None else None,
            result_count=0, retry_count=retries,
            rate_limit_retry_after=retry_after, failure_reason=error,
        )
        return _PageResult(page, body, False)


class BaseProvider(HttpSource):
    endpoint: str

    def _batch(
        self, *, kind: Kind, query: str, items: list[LiteratureRecord],
        pages: list[RetrievalPage], total: int | None,
        cursor: str | None, complete: bool, not_found: bool = False,
    ) -> RetrievalBatch:
        error = next((p.failure_reason for p in reversed(pages) if p.failure_reason), None)
        state: Completeness = (
            "UNKNOWN" if error is not None and not items
            else "PARTIAL" if error is not None or not complete
            else "COMPLETE"
        )
        # Hash the ordered response hashes, not just the last page.
        hashes = [p.response_sha256 for p in pages if p.response_sha256 is not None]
        anchor = pages[0] if pages else None
        return RetrievalBatch(
            provider=self.provider, kind=kind, query=query, items=items, pages=pages,
            requested_url=anchor.requested_url if anchor else self.endpoint,
            parameters=anchor.parameters if anchor else {},
            observed_at_utc=anchor.observed_at_utc if anchor else datetime.now(UTC),
            response_hash=digest_bytes(canonical_json(hashes)) if hashes else None,
            reported_total=total, retrieved_total=len(items), next_cursor=cursor,
            completeness=state, failure_reason=error,
            lookup_status="NOT_FOUND" if not_found else "FOUND" if items else "NOT_APPLICABLE",
        )


class OpenAlexProvider(BaseProvider):
    endpoint = "https://api.openalex.org"

    def __init__(self, *, allow_network: bool = False, client: Any = None) -> None:
        super().__init__("openalex", allow_network=allow_network, client=client)

    async def lookup_identifier(self, identifier: str) -> RetrievalBatch:
        doi = normalize_doi(identifier)
        url = f"{self.endpoint}/works/https://doi.org/{quote(doi, safe='/')}"
        page = await self._fetch(url, {})
        results: list[LiteratureRecord] = []
        if page.body is not None:
            try:
                results = [_normalize_item("openalex", page.body, "works")]
                if results[0].doi != doi:
                    raise LiteratureSourceError("OpenAlex DOI lookup returned different identifier")
            except (LiteratureSourceError, ValueError, ValidationError):
                page.page = page.page.model_copy(update={"failure_reason": "INVALID_WORK"})
                results = []
        report = page.page.model_copy(update={"result_count":len(results)})
        return self._batch(
            kind="works", query=doi, items=results, pages=[report],
            total=0 if page.not_found else len(results) if results else None,
            cursor=None, complete=page.body is not None or page.not_found,
            not_found=page.not_found,
        )

    async def search(self, query: LiteratureQuery) -> RetrievalBatch:
        url = f"{self.endpoint}/{'authors' if query.kind == 'authors' else 'works'}"
        limit = query.limit
        cursor = "*"
        visited: set[str] = set()
        pages: list[RetrievalPage] = []
        records: list[LiteratureRecord] = []
        total: int | None = None
        exhausted = False
        for _ in range(query.max_pages):
            if cursor in visited:
                pages.append(RetrievalPage(
                    requested_url=url, parameters={}, observed_at_utc=datetime.now(UTC),
                    http_status=None, response_sha256=None, result_count=0, retry_count=0,
                    rate_limit_retry_after=None, failure_reason="CURSOR_CYCLE",
                ))
                break
            visited.add(cursor)
            per_page = min(query.page_size, limit - len(records))
            params: dict[str, str | int] = {
                "search": query.text, "per-page": per_page, "cursor": cursor
            }
            result = await self._fetch(url, params)
            page = result.page
            if result.body is None:
                pages.append(page.model_copy(update={"failure_reason": page.failure_reason or "INVALID_RESPONSE"}))
                break
            try:
                rows = result.body["results"]
                metadata = result.body["meta"]
                if not isinstance(rows, list) or not isinstance(metadata, dict):
                    raise ValueError("Invalid OpenAlex page shape")
                raw_total = metadata.get("count")
                if raw_total is not None and (type(raw_total) is not int or raw_total < 0):
                    raise ValueError("Invalid provider reported total")
                next_value = metadata.get("next_cursor")
                if next_value is not None and (not isinstance(next_value, str) or not next_value):
                    raise ValueError("Invalid next cursor")
                normalized = [
                    _normalize_item("openalex", row, query.kind)
                    for row in rows if isinstance(row, dict)
                ]
                if len(normalized) != len(rows) or len(normalized) > per_page:
                    raise ValueError("Malformed record list or oversized page")
                if total is None:
                    total = raw_total
                records.extend(normalized)
                pages.append(page.model_copy(update={"result_count":len(normalized)}))
                cursor = next_value or ""
                if not rows or next_value is None or (total is not None and len(records) >= total):
                    exhausted = True
                    break
                if len(records) >= limit:
                    break
            except (KeyError, ValueError, LiteratureSourceError, ValidationError):
                pages.append(page.model_copy(update={"failure_reason": "INVALID_PROVIDER_PAYLOAD"}))
                break
        return self._batch(
            kind=query.kind, query=query.text, items=records, pages=pages, total=total,
            cursor=cursor or None, complete=exhausted,
        )


class CrossrefProvider(BaseProvider):
    endpoint = "https://api.crossref.org"

    def __init__(self, *, allow_network: bool = False, client: Any = None) -> None:
        super().__init__("crossref", allow_network=allow_network, client=client)

    @staticmethod
    def _message(body: dict[str, Any]) -> dict[str, Any]:
        if body.get("status") != "ok" or not isinstance(body.get("message"), dict):
            raise LiteratureSourceError("Crossref response missing successful message")
        message: dict[str, Any] = body["message"]
        return message

    async def lookup_identifier(self, identifier: str) -> RetrievalBatch:
        doi = normalize_doi(identifier)
        url = f"{self.endpoint}/works/{quote(doi, safe='/')}"
        result = await self._fetch(url, {})
        records: list[LiteratureRecord] = []
        page = result.page
        if result.body is not None:
            try:
                item = self._message(result.body)
                records = [_normalize_item("crossref", item, "works")]
                if records[0].doi != doi:
                    raise LiteratureSourceError("Crossref lookup returned mismatched DOI")
            except (ValueError, ValidationError, LiteratureSourceError):
                page = page.model_copy(update={"failure_reason": "INVALID_WORK"})
                records = []
        return self._batch(
            kind="works", query=doi, items=records,
            pages=[page.model_copy(update={"result_count":len(records)})],
            total=0 if result.not_found else len(records) if records else None,
            cursor=None, complete=result.body is not None or result.not_found,
            not_found=result.not_found,
        )

    async def search(self, query: LiteratureQuery) -> RetrievalBatch:
        if query.kind != "works":
            raise LiteratureInputError("Crossref supports work search, not author search")
        url = f"{self.endpoint}/works"
        pages: list[RetrievalPage] = []
        records: list[LiteratureRecord] = []
        total: int | None = None
        offset = 0
        exhausted = False
        for _ in range(query.max_pages):
            page_size = min(query.page_size, query.limit - len(records))
            params: dict[str, str | int] = {
                "query.bibliographic": query.text, "rows": page_size, "offset": offset
            }
            result = await self._fetch(url, params)
            if result.body is None:
                pages.append(result.page.model_copy(
                    update={"failure_reason": result.page.failure_reason or "INVALID_RESPONSE"}
                ))
                break
            try:
                message = self._message(result.body)
                rows = message["items"]
                count = message["total-results"]
                if (
                    not isinstance(rows, list) or type(count) is not int or count < 0
                    or len(rows) > page_size
                ):
                    raise ValueError("Invalid Crossref pagination")
                normalized = [
                    _normalize_item("crossref", row, "works")
                    for row in rows if isinstance(row, dict)
                ]
                if len(normalized) != len(rows):
                    raise ValueError("Invalid item")
                if total is None:
                    total = count
                records.extend(normalized)
                pages.append(result.page.model_copy(update={"result_count":len(normalized)}))
                offset += len(rows)
                if offset >= count:
                    exhausted = True
                    break
                if not rows:
                    pages.append(result.page.model_copy(update={"failure_reason":"EMPTY_PREMATURE_PAGE"}))
                    break
                if len(records) >= query.limit:
                    break
            except (KeyError, ValueError, LiteratureSourceError, ValidationError):
                pages.append(result.page.model_copy(update={"failure_reason": "INVALID_PROVIDER_PAYLOAD"}))
                break
        return self._batch(
            kind="works", query=query.text, items=records, pages=pages,
            total=total, cursor=str(offset) if not exhausted else None,
            complete=exhausted,
        )


def group_by_doi(batches: list[RetrievalBatch]) -> dict[str, list[LiteratureRecord]]:
    """Exact DOI grouping only; author/title resemblance never implies identity."""
    groups: dict[str, list[LiteratureRecord]] = {}
    for batch in batches:
        for item in batch.items:
            key = f"doi:{item.doi}" if item.doi else f"{item.provider}:{item.provider_id}"
            groups.setdefault(key, []).append(item)
    return groups


async def run_lookup(
    identifier: str, *, sources: tuple[ProviderName, ...],
    allow_network: bool = False, clients: dict[str, Any] | None = None,
) -> list[RetrievalBatch]:
    doi = normalize_doi(identifier)
    clients = clients or {}
    providers: dict[str, LiteratureProvider] = {
        "crossref": CrossrefProvider(allow_network=allow_network, client=clients.get("crossref")),
        "openalex": OpenAlexProvider(allow_network=allow_network, client=clients.get("openalex")),
    }
    return [await providers[source].lookup_identifier(doi) for source in sources]


async def run_search(
    query: LiteratureQuery, *, sources: tuple[ProviderName, ...],
    allow_network: bool = False, clients: dict[str, Any] | None = None,
) -> list[RetrievalBatch]:
    clients = clients or {}
    providers: dict[str, LiteratureProvider] = {
        "crossref": CrossrefProvider(allow_network=allow_network, client=clients.get("crossref")),
        "openalex": OpenAlexProvider(allow_network=allow_network, client=clients.get("openalex")),
    }
    return [await providers[source].search(query) for source in sources]


def parse_sources(raw: str) -> tuple[ProviderName, ...]:
    pieces = [part.strip().lower() for part in raw.split(",")]
    if not pieces or any(part not in {"openalex", "crossref"} for part in pieces):
        raise LiteratureInputError("Sources must be crossref,openalex")
    if len(set(pieces)) != len(pieces):
        raise LiteratureInputError("Duplicate literature provider")
    return cast(tuple[ProviderName, ...], tuple(pieces))

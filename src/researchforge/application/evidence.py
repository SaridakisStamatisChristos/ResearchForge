"""Append-only campaign event ledger with chain and blob verification.

No process executions or result scoring occur here. Integrity means locally recorded
bytes are self-consistent, not that an experiment was truly or safely executed.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from researchforge.adapters.cas import ArtifactIntegrityError, FileSystemCAS
from researchforge.adapters.sqlite.connection import open_ledger_db
from researchforge.domain.models import EventActor, EvidenceEvent
from researchforge.domain.serialization import canonical_json, digest_bytes


class SequenceConflict(Exception):
    """Observed sequence did not match caller's optimistic lease."""


class LedgerIntegrityError(Exception):
    """Stored event, sequence or blob fails independent integrity verification."""


class EventLedger:
    """Durable single-writer per transaction, with SQLite enforcing sequence uniqueness."""

    def __init__(self, campaign: Path) -> None:
        self.root = campaign.resolve() / ".researchforge"
        self.cas = FileSystemCAS(self.root / "blobs")
        self.database = self.root / "ledger.sqlite3"
        with closing(self._connect()) as connection:
            connection.execute("SELECT 1")

    def _connect(self) -> sqlite3.Connection:
        return open_ledger_db(self.database)

    def append_event(
        self,
        campaign_id: str,
        expected_seq: int,
        event_type: str,
        subject_id: str,
        payload: dict[str, Any],
        *,
        actor: str = "rf-cli",
        inject_failure: Callable[[str], None] | None = None,
    ) -> EvidenceEvent:
        """Publish canonical blob, then atomically insert next event or fail closed.

        Fault injection is for tests; a crash after publication may leave an orphan blob,
        but cannot create a phantom committed event.
        """
        if not campaign_id or not event_type or not subject_id or not actor or expected_seq < 0:
            raise ValueError("Invalid event identity or expected sequence")
        content = canonical_json(payload)
        ref = self.cas.put(content)
        if inject_failure is not None:
            inject_failure("after_blob")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT sequence, event_sha256 FROM events "
                "WHERE campaign_id=? ORDER BY sequence DESC LIMIT 1",
                (campaign_id,),
            ).fetchone()
            sequence = int(row[0]) if row else 0
            if sequence != expected_seq:
                raise SequenceConflict(f"Expected {expected_seq}, found {sequence}")
            prior = str(row[1]) if row else None
            preliminary = EvidenceEvent(
                event_schema="1.0",
                event_id=uuid4(),
                campaign_id=campaign_id,
                sequence=sequence + 1,
                event_type=event_type,
                occurred_at_utc=datetime.now(UTC),
                actor=EventActor(kind="local_process", id=actor),
                subject_id=subject_id,
                payload_ref=ref,
                previous_event_sha256=prior,
                event_sha256="0" * 64,
            )
            event_hash = digest_bytes(
                canonical_json(preliminary.model_dump(mode="json", exclude={"event_sha256"}))
            )
            event = preliminary.model_copy(update={"event_sha256": event_hash})
            serialized = canonical_json(event.model_dump(mode="json")).decode("utf-8")
            connection.execute(
                "INSERT INTO events "
                "(campaign_id,sequence,event_id,event_json,payload_sha256,event_sha256) "
                "VALUES (?,?,?,?,?,?)",
                (
                    campaign_id,
                    sequence + 1,
                    str(event.event_id),
                    serialized,
                    ref.sha256,
                    event_hash,
                ),
            )
            if inject_failure is not None:
                inject_failure("before_commit")
            connection.execute("COMMIT")
            return event
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def read_events(self, campaign_id: str) -> list[EvidenceEvent]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT event_json FROM events WHERE campaign_id=? ORDER BY sequence",
                (campaign_id,),
            ).fetchall()
        try:
            return [EvidenceEvent.model_validate_json(row[0]) for row in rows]
        except (ValidationError, ValueError) as exc:
            raise LedgerIntegrityError("Malformed stored event") from exc

    def verify(self, campaign_id: str) -> dict[str, str | int | None]:
        """Offline replay: no subprocess, network or data mutation."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT sequence,event_id,event_json,payload_sha256,event_sha256 "
                "FROM events WHERE campaign_id=? ORDER BY sequence",
                (campaign_id,),
            ).fetchall()
        head: str | None = None
        for number, (seq, event_id, content, payload_sha, stored_hash) in enumerate(rows, 1):
            try:
                event = EvidenceEvent.model_validate_json(content)
            except (ValidationError, ValueError, TypeError) as exc:
                raise LedgerIntegrityError(f"Malformed event {number}") from exc
            expected_hash = digest_bytes(
                canonical_json(event.model_dump(mode="json", exclude={"event_sha256"}))
            )
            if content != canonical_json(event.model_dump(mode="json")).decode("utf-8"):
                raise LedgerIntegrityError(f"Noncanonical event JSON at {number}")
            if (
                seq != number
                or event.sequence != number
                or event.campaign_id != campaign_id
                or str(event.event_id) != event_id
                or event.previous_event_sha256 != head
                or stored_hash != event.event_sha256
                or expected_hash != event.event_sha256
                or payload_sha != event.payload_ref.sha256
            ):
                raise LedgerIntegrityError(f"Broken chain or row mismatch at {number}")
            try:
                payload_bytes = self.cas.get(event.payload_ref)
                if canonical_json(json.loads(payload_bytes)) != payload_bytes:
                    raise LedgerIntegrityError("Noncanonical payload bytes")
            except (ArtifactIntegrityError, ValueError, UnicodeDecodeError) as exc:
                raise LedgerIntegrityError(f"Payload corruption at {number}") from exc
            head = stored_hash
        return {"campaign_id": campaign_id, "event_count": len(rows), "chain_head": head}

    def orphan_digests(self) -> set[str]:
        """Read-only inventory for later recovery tooling; never sweep without authorization."""
        with closing(self._connect()) as connection:
            references = {
                str(row[0])
                for row in connection.execute("SELECT DISTINCT payload_sha256 FROM events")
            }
        return self.cas.all_digests() - references

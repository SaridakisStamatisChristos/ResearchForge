"""Integration checks of actual SQLite/WAL and filesystem CAS."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from researchforge.adapters.cas import ArtifactIntegrityError, FileSystemCAS
from researchforge.application.evidence import EventLedger, LedgerIntegrityError, SequenceConflict
from researchforge.domain.serialization import canonical_json, digest_bytes


def test_canonical_bytes_unicode_and_nonfinite() -> None:
    assert canonical_json({"Ω": 1, "a": 2}) == canonical_json({"a": 2, "Ω": 1})
    with pytest.raises(ValueError):
        canonical_json({"x": float("inf")})


def test_cas_publication_and_integrity(tmp_path: Path) -> None:
    store = FileSystemCAS(tmp_path / "blobs")
    first = store.put(b"evidence")
    assert store.put(b"evidence") == first
    assert store.verify(first)
    assert first.sha256 == digest_bytes(b"evidence")
    assert store.get(first) == b"evidence"
    target = store._path(first.sha256)
    target.write_bytes(b"tampered")
    assert not store.verify(first)
    with pytest.raises(ArtifactIntegrityError):
        store.put(b"evidence")
    with pytest.raises(ArtifactIntegrityError):
        store.get(first)


def test_cas_refuses_symlink_at_address(tmp_path: Path) -> None:
    store = FileSystemCAS(tmp_path / "blobs")
    digest = digest_bytes(b"x")
    addr = store._path(digest)
    addr.parent.mkdir(parents=True)
    outside = tmp_path / "unowned"
    outside.write_bytes(b"x")
    addr.symlink_to(outside)
    with pytest.raises(ArtifactIntegrityError):
        store.put(b"x")
    assert outside.read_bytes() == b"x"


def test_chain_append_verify_and_sequence_conflict(tmp_path: Path) -> None:
    ledger = EventLedger(tmp_path)
    first = ledger.append_event("camp", 0, "CAMPAIGN_CREATED", "camp", {"x": 1})
    second = ledger.append_event("camp", 1, "NOTE", "n-1", {"text": "Ω"})
    assert first.sequence == 1 and second.sequence == 2
    assert second.previous_event_sha256 == first.event_sha256
    assert ledger.verify("camp")["event_count"] == 2
    assert ledger.read_events("camp") == [first, second]
    with pytest.raises(SequenceConflict):
        ledger.append_event("camp", 0, "DUPLICATE", "camp", {"x": 1})
    assert ledger.verify("camp")["event_count"] == 2


def test_integrity_detects_sqlite_mutation(tmp_path: Path) -> None:
    ledger = EventLedger(tmp_path)
    ledger.append_event("camp", 0, "CREATED", "camp", {"x": 1})
    with sqlite3.connect(ledger.database) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE events SET event_sha256=?", ("a" * 64,))
        db.execute("DROP TRIGGER events_no_update")
        db.execute("UPDATE events SET event_sha256=?", ("a" * 64,))
    with pytest.raises(LedgerIntegrityError):
        ledger.verify("camp")


def test_blob_tamper_caught_by_offline_verification(tmp_path: Path) -> None:
    ledger = EventLedger(tmp_path)
    record = ledger.append_event("camp", 0, "CREATED", "camp", {"x": 1})
    ledger.cas._path(record.payload_ref.sha256).write_bytes(b"changed")
    with pytest.raises(LedgerIntegrityError):
        ledger.verify("camp")


def test_concurrent_writers_one_commits(tmp_path: Path) -> None:
    ledger = EventLedger(tmp_path)

    def append(number: int) -> str:
        try:
            ledger.append_event("camp", 0, "ATTEMPT", f"writer-{number}", {"id": number})
            return "ok"
        except SequenceConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(append, [1, 2]))
    assert sorted(outcomes) == ["conflict", "ok"]
    assert ledger.verify("camp")["event_count"] == 1


def test_collision_rejected_even_when_digest_matches(tmp_path: Path) -> None:
    with patch("researchforge.adapters.cas.digest_bytes", return_value="a" * 64):
        store = FileSystemCAS(tmp_path / "blobs")
        store.put(b"A")
        with pytest.raises(ArtifactIntegrityError):
            store.put(b"B")

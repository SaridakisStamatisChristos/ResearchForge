"""Crash-window fault injection and conservative recovery evidence."""

from __future__ import annotations

from pathlib import Path

import pytest

from researchforge.application.evidence import EventLedger
from researchforge.domain.serialization import canonical_json, digest_bytes


def test_blob_orphan_when_crash_after_publication(tmp_path: Path) -> None:
    ledger = EventLedger(tmp_path)

    def crash(stage: str) -> None:
        if stage == "after_blob":
            raise RuntimeError("simulated crash")

    with pytest.raises(RuntimeError):
        ledger.append_event("camp", 0, "START", "c", {"data": 1}, inject_failure=crash)
    assert ledger.verify("camp")["event_count"] == 0
    assert digest_bytes(canonical_json({"data": 1})) in ledger.orphan_digests()


def test_transaction_rolls_back_before_commit(tmp_path: Path) -> None:
    ledger = EventLedger(tmp_path)

    def crash(stage: str) -> None:
        if stage == "before_commit":
            raise RuntimeError("simulated transaction interruption")

    with pytest.raises(RuntimeError):
        ledger.append_event("camp", 0, "START", "c", {"data": 2}, inject_failure=crash)
    assert ledger.verify("camp")["event_count"] == 0
    assert ledger.append_event("camp", 0, "RETRY", "c", {"data": 2}).sequence == 1
    assert ledger.verify("camp")["event_count"] == 1


def test_stable_sequence_across_reopen(tmp_path: Path) -> None:
    ledger = EventLedger(tmp_path)
    ledger.append_event("camp", 0, "CREATED", "camp", {"x": 1})
    reopened = EventLedger(tmp_path)
    reopened.append_event("camp", 1, "NOTE", "n-1", {"x": 2})
    assert reopened.verify("camp")["event_count"] == 2

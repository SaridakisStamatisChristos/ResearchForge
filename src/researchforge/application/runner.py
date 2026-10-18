"""Explicitly approved, trusted-local evaluator execution with durable provenance.

Not a security sandbox. An evaluator can access host resources; never run adversarial code.
"""

from __future__ import annotations

import json
import os
import platform
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from researchforge.application.campaigns import (
    CampaignService,
    FrozenStateError,
    ManifestError,
    _git,
    _repository_path,
    _safe_child,
)
from researchforge.application.evidence import EventLedger, LedgerIntegrityError
from researchforge.domain.models import EvaluationResult
from researchforge.domain.serialization import digest_bytes


class ExecutionRefused(Exception):
    """The caller has not approved trusted execution or a prerequisite is unsatisfied."""


class RunnerIntegrityError(Exception):
    """The ledger or frozen inputs are inconsistent; no execution is permitted."""


TERMINAL = {"ATTEMPT_SUCCEEDED", "ATTEMPT_FAILED", "ATTEMPT_TIMED_OUT", "ATTEMPT_UNKNOWN"}


def _pairs_unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate evaluator JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"Nonfinite evaluator output: {value}")


def _result(stdout: bytes, *, manifest: Any, expected_hash: str, count: int) -> EvaluationResult:
    try:
        obj = json.loads(
            stdout.decode("utf-8"),
            object_pairs_hook=_pairs_unique,
            parse_constant=_reject_constant,
        )
        if not isinstance(obj, dict):
            raise ValueError("Evaluator output is not an object")
        result = EvaluationResult.model_validate(obj)
    except (UnicodeError, ValueError, ValidationError) as exc:
        raise ValueError("Malformed evaluator JSON result") from exc
    if (
        result.metric != manifest.metric.name
        or result.direction != manifest.metric.direction
        or result.unit != manifest.metric.unit
        or result.samples_sha256 != expected_hash
        or result.sample_count != count
        or not result.correctness.passed
    ):
        raise ValueError("Evaluator result contradicts frozen metric, split or correctness")
    return result


def _bounded_process(
    argv: list[str], *, cwd: Path, timeout: int, max_output: int, env: dict[str, str]
) -> tuple[int | None, bytes, bytes, str | None, float]:
    """Drain both pipes continuously; kill process group on timeout/output excess."""
    started = time.monotonic()
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=os.name == "posix",
    )
    out, err = bytearray(), bytearray()
    lock = threading.Lock()
    exceeded = threading.Event()
    total = 0

    def drain(stream: Any, buffer: bytearray) -> None:
        nonlocal total
        try:
            while chunk := stream.read(65536):
                with lock:
                    keep = min(len(chunk), max(0, max_output - total))
                    buffer.extend(chunk[:keep])
                    total += keep
                    if keep != len(chunk):
                        exceeded.set()
        finally:
            stream.close()

    assert proc.stdout is not None and proc.stderr is not None
    readers = [
        threading.Thread(target=drain, args=(proc.stdout, out), daemon=True),
        threading.Thread(target=drain, args=(proc.stderr, err), daemon=True),
    ]
    for reader in readers:
        reader.start()
    reason: str | None = None
    try:
        while proc.poll() is None:
            if exceeded.is_set():
                reason = "OUTPUT_LIMIT"
                break
            if time.monotonic() - started >= timeout:
                reason = "TIMEOUT"
                break
            time.sleep(0.02)
        if reason is not None:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        proc.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        reason = "UNKNOWN"
        try:
            proc.kill()
            proc.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            pass
    for reader in readers:
        reader.join(timeout=3)
    if any(reader.is_alive() for reader in readers):
        reason = "UNKNOWN"
    if exceeded.is_set():
        reason = "OUTPUT_LIMIT" if reason is None else reason
    if reason == "TIMEOUT" and os.name != "posix":
        reason = "UNKNOWN"
    return proc.returncode, bytes(out), bytes(err), reason, time.monotonic() - started


class RunnerService:
    """Low-level attempts only. PR05 will add hypothesis/candidate state transitions."""

    def __init__(self, path: Path) -> None:
        self.campaign = CampaignService(path)

    def _ready(self) -> tuple[Any, Any, Path, Path, EventLedger]:
        try:
            record = self.campaign.inspect()
            snapshot = self.campaign.validate()
            manifest, evaluator_root, _ = self.campaign._manifest()
            repository = _repository_path(manifest.base.repository)
            ledger = EventLedger(self.campaign.root)
            ledger.verify(snapshot.campaign_id)
            if record["campaign"]["manifest_sha256"] != snapshot.manifest_sha256:
                raise RunnerIntegrityError("Frozen manifest mismatch")
        except (ManifestError, FrozenStateError, LedgerIntegrityError, ValueError) as exc:
            raise RunnerIntegrityError("Cannot establish trustworthy frozen campaign") from exc
        return manifest, snapshot, evaluator_root, repository, ledger

    @staticmethod
    def _open_attempts(ledger: EventLedger, campaign_id: str) -> dict[str, dict[str, Any]]:
        pending: dict[str, dict[str, Any]] = {}
        for event in ledger.read_events(campaign_id):
            if event.event_type == "ATTEMPT_STARTED":
                if event.subject_id in pending:
                    raise RunnerIntegrityError("Duplicate start for attempt")
                pending[event.subject_id] = json.loads(ledger.cas.get(event.payload_ref))
            elif event.event_type in TERMINAL:
                if event.subject_id not in pending:
                    raise RunnerIntegrityError("Terminal attempt missing start")
                del pending[event.subject_id]
        return pending

    @contextmanager
    def _lease(self) -> Iterator[None]:
        """Single runner/recovery writer; OS releases lock after an interrupted process."""
        root = self.campaign.root / ".researchforge" / "lock"
        root.mkdir(parents=True, exist_ok=True)
        with (root / "runner.lock").open("a+b") as handle:
            try:
                if os.name == "posix":
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    import msvcrt

                    handle.write(b"0")
                    handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except (OSError, BlockingIOError) as exc:
                raise ExecutionRefused(
                    "A runner or recovery operation already owns this campaign"
                ) from exc
            try:
                yield
            finally:
                if os.name == "posix":
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                else:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

    def recover_unknown(self, *, approved: bool) -> list[str]:
        """Explicit operator adjudication. Never replay a possibly executed command."""
        if not approved:
            raise ExecutionRefused("Explicit --mark-unknown approval is required")
        with self._lease():
            _, snapshot, _, _, ledger = self._ready()
            pending = self._open_attempts(ledger, snapshot.campaign_id)
            ids: list[str] = []
            for attempt_id in pending:
                sequence = int(ledger.verify(snapshot.campaign_id)["event_count"])
                ledger.append_event(
                    snapshot.campaign_id,
                    sequence,
                    "ATTEMPT_UNKNOWN",
                    attempt_id,
                    {"reason": "INTERRUPTED_OUTCOME_UNPROVEN", "retry_automatic": False},
                )
                ids.append(attempt_id)
            return ids

    def run(self, *, role: str, approved: bool) -> dict[str, Any]:
        if not approved:
            raise ExecutionRefused("Explicit --approve-trusted-local flag required")
        with self._lease():
            return self._run_locked(role=role)

    def _run_locked(self, *, role: str) -> dict[str, Any]:
        if role not in ("dev", "qualification"):
            raise ExecutionRefused("Final split is sealed until PR06")
        manifest, snapshot, evaluator_root, repository, ledger = self._ready()
        if self._open_attempts(ledger, snapshot.campaign_id):
            raise ExecutionRefused("Unresolved attempt: adjudicate UNKNOWN; no auto replay")
        previous = ledger.read_events(snapshot.campaign_id)
        attempts = sum(event.event_type == "ATTEMPT_STARTED" for event in previous)
        if attempts >= manifest.budget.max_attempts:
            raise ExecutionRefused("Campaign evaluation-attempt budget exhausted")
        wall_used = 0.0
        for event in previous:
            if event.event_type in TERMINAL:
                prior = json.loads(ledger.cas.get(event.payload_ref))
                wall_used += float(prior.get("duration_seconds") or 0)
                if event.event_type == "ATTEMPT_UNKNOWN":
                    wall_used += manifest.evaluation.timeout_seconds
        remaining_wall = manifest.budget.max_wall_seconds - wall_used
        if remaining_wall <= 0:
            raise ExecutionRefused("Campaign evaluation wall-time budget exhausted")
        if manifest.execution_trust != "local_trusted_only":
            raise ExecutionRefused("Only trusted local campaigns are supported")
        spec = manifest.evaluation
        if spec.cwd not in ("{evaluator_dir}", "{worktree}"):
            raise ExecutionRefused("Evaluator cwd must be an approved rooted placeholder")
        split = snapshot.splits[role]
        dataset = _safe_child(evaluator_root, split.relative_path)
        attempt_id = uuid4().hex
        worktree = self.campaign.root / ".researchforge" / "worktrees" / attempt_id
        if not worktree.parent.resolve().is_relative_to(self.campaign.root):
            raise ExecutionRefused("Worktree path escapes campaign")
        replacements = {
            "{split_file}": str(dataset),
            "{worktree}": str(worktree),
            "{evaluator_dir}": str(evaluator_root),
        }
        argv: list[str] = []
        for token in spec.argv:
            for placeholder in ("{split_file}", "{worktree}", "{evaluator_dir}"):
                if placeholder in token and token != placeholder:
                    raise ExecutionRefused("Placeholders must occupy entire argv elements")
            if "{" in token or "}" in token:
                if token not in replacements:
                    raise ExecutionRefused("Unknown or embedded argv placeholder")
            argv.append(replacements.get(token, token))
        cwd = Path(replacements[spec.cwd])
        sequence = int(ledger.verify(snapshot.campaign_id)["event_count"])
        ledger.append_event(
            snapshot.campaign_id,
            sequence,
            "ATTEMPT_STARTED",
            attempt_id,
            {
                "attempt_id": attempt_id,
                "role": role,
                "commit": snapshot.repository_commit,
                "tree": snapshot.repository_tree,
                "split_sha256": split.sha256,
                "evaluator_sha256": snapshot.evaluator_source_sha256,
                "environment_sha256": snapshot.environment_sha256,
                "argv": argv,
                "cwd": str(cwd),
                "worktree": str(worktree),
                "started_at_utc": datetime.now(UTC).isoformat(),
                "explicit_approval": True,
            },
        )
        status = "FAILED"
        reason: str | None = None
        code: int | None = None
        stdout = stderr = b""
        duration = 0.0
        verified: EvaluationResult | None = None
        try:
            worktree.parent.mkdir(parents=True, exist_ok=True)
            _git(
                repository, "worktree", "add", "--detach", str(worktree),
                snapshot.repository_commit,
            )
            if (
                _git(worktree, "rev-parse", "HEAD") != snapshot.repository_commit
                or _git(worktree, "rev-parse", "HEAD^{tree}") != snapshot.repository_tree
            ):
                raise ValueError("Exact worktree commit/tree mismatch")
            # Export only minimal host variables, not inherited API keys/credentials.
            env = {
                key: val for key, val in os.environ.items()
                if key in {
                    "PATH", "SYSTEMROOT", "WINDIR", "LANG",
                    "LC_ALL", "TMPDIR", "TEMP", "TMP",
                }
            }
            env.update({
                "RF_WORKTREE": str(worktree),
                "RF_SPLIT_FILE": str(dataset),
                "RF_SEED": str(manifest.seed),
                "PYTHONIOENCODING": "utf-8",
            })
            code, stdout, stderr, issue, duration = _bounded_process(
                argv,
                cwd=cwd,
                timeout=min(spec.timeout_seconds, max(1, int(remaining_wall))),
                max_output=spec.max_output_bytes,
                env=env,
            )
            if issue is not None:
                if issue == "UNKNOWN":
                    status = "UNKNOWN"
                elif issue == "TIMEOUT":
                    status = "TIMED_OUT"
                reason = issue
            elif code != 0:
                reason = "NONZERO_EXIT"
            elif (
                _git(worktree, "rev-parse", "HEAD") != snapshot.repository_commit
                or _git(worktree, "rev-parse", "HEAD^{tree}") != snapshot.repository_tree
                or subprocess.run(
                    ["git", "-C", str(worktree), "diff", "--quiet", "HEAD"],
                    capture_output=True, timeout=10, check=False,
                ).returncode != 0
            ):
                reason = "WORKTREE_MUTATED"
            elif any(
                digest_bytes(_safe_child(evaluator_root, s.relative_path).read_bytes()) != s.sha256
                for s in snapshot.splits.values()
            ):
                reason = "SPLIT_MUTATED"
            else:
                try:
                    self.campaign.inspect()
                    verified = _result(
                        stdout,
                        manifest=manifest,
                        expected_hash=split.sha256,
                        count=split.case_count,
                    )
                    status = "SUCCEEDED"
                except (FrozenStateError, ManifestError, ValueError) as exc:
                    reason = type(exc).__name__
        except (OSError, ManifestError, ValueError, subprocess.SubprocessError) as exc:
            reason = type(exc).__name__
        output_ref = ledger.cas.put(stdout)
        error_ref = ledger.cas.put(stderr)
        payload: dict[str, Any] = {
            "attempt_id": attempt_id,
            "status": status,
            "reason": reason,
            "exit_code": code,
            "duration_seconds": duration,
            "stdout_ref": output_ref.model_dump(mode="json"),
            "stderr_ref": error_ref.model_dump(mode="json"),
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "validated_result": None,
        }
        if verified is not None:
            result_ref = ledger.cas.put(stdout)
            payload["validated_result"] = verified.model_dump(mode="json")
            payload["result_ref"] = result_ref.model_dump(mode="json")
        event_type = {
            "SUCCEEDED": "ATTEMPT_SUCCEEDED",
            "FAILED": "ATTEMPT_FAILED",
            "TIMED_OUT": "ATTEMPT_TIMED_OUT",
            "UNKNOWN": "ATTEMPT_UNKNOWN",
        }[status]
        sequence = int(ledger.verify(snapshot.campaign_id)["event_count"])
        ledger.append_event(snapshot.campaign_id, sequence, event_type, attempt_id, payload)
        return {
            "attempt_id": attempt_id,
            "status": status,
            "reason": reason,
            "event_type": event_type,
        }

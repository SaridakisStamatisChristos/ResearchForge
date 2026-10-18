"""Append-only hypothesis/candidate records and deterministic selection-stage qualification.

Only PR04 validated subprocess evidence is eligible. Qualification is NOT final proof.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError

from researchforge.application.campaigns import _git
from researchforge.application.evidence import EventLedger
from researchforge.application.runner import (
    ExecutionRefused,
    RunnerIntegrityError,
    RunnerService,
    _result,
)
from researchforge.domain.models import ArtifactRef, EvaluationResult, StrictModel
from researchforge.domain.serialization import canonical_json


class HypothesisSpec(StrictModel):
    hypothesis_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    proposition: str = Field(min_length=1)
    intervention: str = Field(min_length=1)
    falsifier: str = Field(min_length=1)
    parent_id: str | None = None


class QualificationService:
    """All writes serialize through the trusted runner's campaign file lease."""

    def __init__(self, path: Path) -> None:
        self.runner = RunnerService(path)

    @staticmethod
    def _payloads(ledger: EventLedger, campaign_id: str) -> list[tuple[Any, dict[str, Any]]]:
        entries: list[tuple[Any, dict[str, Any]]] = []
        for event in ledger.read_events(campaign_id):
            try:
                raw = ledger.cas.get(event.payload_ref)
                value = json.loads(raw)
                if not isinstance(value, dict) or canonical_json(value) != raw:
                    raise ValueError("Noncanonical stored event payload")
                entries.append((event, value))
            except (TypeError, ValueError) as exc:
                raise RunnerIntegrityError("Malformed event payload") from exc
        return entries

    @classmethod
    def registry(
        cls, ledger: EventLedger, campaign_id: str
    ) -> tuple[dict[str, HypothesisSpec], dict[str, dict[str, str]]]:
        hypotheses: dict[str, HypothesisSpec] = {}
        candidates: dict[str, dict[str, str]] = {}
        for event, payload in cls._payloads(ledger, campaign_id):
            if event.event_type == "HYPOTHESIS_PROPOSED":
                try:
                    spec = HypothesisSpec.model_validate(payload)
                except ValidationError as exc:
                    raise RunnerIntegrityError("Invalid stored hypothesis") from exc
                if event.subject_id != spec.hypothesis_id or spec.hypothesis_id in hypotheses:
                    raise RunnerIntegrityError("Duplicate or mismatched hypothesis")
                if spec.parent_id is not None and spec.parent_id not in hypotheses:
                    raise RunnerIntegrityError("Unresolved hypothesis parent")
                hypotheses[spec.hypothesis_id] = spec
            elif event.event_type == "CANDIDATE_REGISTERED":
                if (
                    set(payload) != {"candidate_id", "hypothesis_id", "commit", "tree"}
                    or any(not isinstance(v, str) for v in payload.values())
                    or event.subject_id != payload.get("candidate_id")
                    or payload["candidate_id"] in candidates
                    or payload["hypothesis_id"] not in hypotheses
                    or not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", payload["commit"])
                    or not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", payload["tree"])
                ):
                    raise RunnerIntegrityError("Invalid immutable candidate identity")
                candidates[payload["candidate_id"]] = payload
        return hypotheses, candidates

    @staticmethod
    def _append(
        ledger: EventLedger,
        campaign_id: str,
        event_type: str,
        subject_id: str,
        payload: dict[str, Any],
    ) -> Any:
        count = ledger.verify(campaign_id)["event_count"]
        if not isinstance(count, int):
            raise RunnerIntegrityError("Invalid ledger event count")
        return ledger.append_event(campaign_id, count, event_type, subject_id, payload)

    def add_hypothesis(self, spec: HypothesisSpec) -> dict[str, Any]:
        with self.runner._lease():
            manifest, frozen, _, _, ledger = self.runner._ready()
            hypotheses, _ = self.registry(ledger, frozen.campaign_id)
            if spec.hypothesis_id in hypotheses:
                if hypotheses[spec.hypothesis_id] != spec:
                    raise ExecutionRefused("Hypothesis ID cannot be reused with other contents")
                return {"hypothesis_id": spec.hypothesis_id, "status": "EXISTS"}
            if len(hypotheses) >= manifest.budget.max_hypotheses:
                raise ExecutionRefused("Hypothesis budget exhausted")
            if spec.parent_id is not None and spec.parent_id not in hypotheses:
                raise ExecutionRefused("Parent hypothesis does not exist")
            self._append(
                ledger,
                frozen.campaign_id,
                "HYPOTHESIS_PROPOSED",
                spec.hypothesis_id,
                spec.model_dump(mode="json"),
            )
            return {"hypothesis_id": spec.hypothesis_id, "status": "PROPOSED"}

    def register(self, *, candidate_id: str, hypothesis_id: str, commit: str) -> dict[str, str]:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", candidate_id):
            raise ExecutionRefused("Invalid candidate ID")
        if not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", commit):
            raise ExecutionRefused("Candidate must specify a full immutable Git OID")
        with self.runner._lease():
            manifest, frozen, _, repo, ledger = self.runner._ready()
            hypotheses, candidates = self.registry(ledger, frozen.campaign_id)
            if hypothesis_id not in hypotheses:
                raise ExecutionRefused("Unknown hypothesis")
            if commit == frozen.repository_commit:
                raise ExecutionRefused("Candidate equals the frozen baseline")
            resolved = _git(
                repo, "rev-parse", "--verify", "--end-of-options", f"{commit}^{{commit}}"
            )
            if resolved != commit:
                raise ExecutionRefused("Candidate must name a full commit object ID")
            tree = _git(repo, "rev-parse", "--verify", "--end-of-options", f"{commit}^{{tree}}")
            payload = {
                "candidate_id": candidate_id,
                "hypothesis_id": hypothesis_id,
                "commit": commit,
                "tree": tree,
            }
            if candidate_id in candidates:
                if candidates[candidate_id] != payload:
                    raise ExecutionRefused("Candidate identity cannot be replaced")
                return payload
            if any(existing["commit"] == commit for existing in candidates.values()):
                raise ExecutionRefused("Commit already registered as another candidate")
            if len(candidates) >= manifest.budget.max_hypotheses:
                raise ExecutionRefused("Candidate count budget exhausted")
            self._append(ledger, frozen.campaign_id, "CANDIDATE_REGISTERED", candidate_id, payload)
            return payload

    def _measurements(
        self,
        ledger: EventLedger,
        campaign_id: str,
        manifest: Any,
        frozen: Any,
        candidates: dict[str, dict[str, str]],
    ) -> tuple[dict[tuple[str, str], tuple[Any, EvaluationResult]], int]:
        starts: dict[str, tuple[Any, dict[str, Any]]] = {}
        closed: set[str] = set()
        measurements: dict[tuple[str, str], tuple[Any, EvaluationResult]] = {}
        exposure = 0
        terminal = {"ATTEMPT_SUCCEEDED", "ATTEMPT_FAILED", "ATTEMPT_TIMED_OUT", "ATTEMPT_UNKNOWN"}
        for event, payload in self._payloads(ledger, campaign_id):
            if event.event_type == "ATTEMPT_STARTED":
                ident = payload.get("candidate_id", "baseline")
                role = payload.get("role")
                target_commit = (
                    frozen.repository_commit
                    if ident == "baseline"
                    else candidates.get(str(ident), {}).get("commit")
                )
                target_tree = (
                    frozen.repository_tree
                    if ident == "baseline"
                    else candidates.get(str(ident), {}).get("tree")
                )
                if (
                    event.subject_id in starts
                    or event.subject_id in closed
                    or not isinstance(ident, str)
                    or role not in {"dev", "qualification"}
                    or target_commit is None
                    or payload.get("commit") != target_commit
                    or payload.get("tree") != target_tree
                    or payload.get("split_sha256") != frozen.splits[role].sha256
                    or payload.get("evaluator_sha256") != frozen.evaluator_source_sha256
                    or payload.get("environment_sha256") != frozen.environment_sha256
                    or payload.get("explicit_approval") is not True
                ):
                    raise RunnerIntegrityError("Untrusted or mismatched attempt start")
                # A later failure or UNKNOWN cannot leave a stale successful score selectable.
                measurements.pop((ident, role), None)
                starts[event.subject_id] = (event, payload)
                if role == "qualification" and ident != "baseline":
                    exposure += 1
            elif event.event_type in terminal:
                if event.subject_id not in starts or event.subject_id in closed:
                    raise RunnerIntegrityError("Attempt terminal has no unique start")
                started_event, start = starts.pop(event.subject_id)
                closed.add(event.subject_id)
                ident = str(start.get("candidate_id", "baseline"))
                role = str(start["role"])
                if event.event_type == "ATTEMPT_SUCCEEDED":
                    try:
                        if payload["status"] != "SUCCEEDED" or payload["exit_code"] != 0:
                            raise ValueError("Unsuccessful evaluator")
                        stdout = ledger.cas.get(ArtifactRef.model_validate(payload["stdout_ref"]))
                        result = _result(
                            stdout,
                            manifest=manifest,
                            expected_hash=frozen.splits[role].sha256,
                            count=frozen.splits[role].case_count,
                        )
                        if (
                            payload["validated_result"] != result.model_dump(mode="json")
                            or ledger.cas.get(ArtifactRef.model_validate(payload["result_ref"]))
                            != stdout
                            or start["attempt_id"] != event.subject_id
                            or started_event.sequence >= event.sequence
                        ):
                            raise ValueError("Terminal score or provenance inconsistent")
                    except (KeyError, TypeError, ValueError, ValidationError) as exc:
                        raise RunnerIntegrityError("Unverifiable successful attempt") from exc
                    measurements[(ident, role)] = (event, result)
        return measurements, exposure

    def decide(self, candidate_id: str) -> dict[str, Any]:
        """Deterministic comparison of four independently revalidated output records."""
        manifest, frozen, _, _, ledger = self.runner._ready()
        _, candidates = self.registry(ledger, frozen.campaign_id)
        if candidate_id not in candidates:
            raise ExecutionRefused("Candidate is not registered")
        measurements, exposure = self._measurements(
            ledger, frozen.campaign_id, manifest, frozen, candidates
        )
        referenced: dict[str, str] = {}
        for who, role in (
            ("baseline", "dev"),
            (candidate_id, "dev"),
            ("baseline", "qualification"),
            (candidate_id, "qualification"),
        ):
            row = measurements.get((who, role))
            if row is not None:
                referenced[f"{who}:{role}"] = row[0].event_sha256
        status, reason = "INCONCLUSIVE", "MISSING_VERIFIED_EVIDENCE"
        gain: float | None = None
        if all(
            key in referenced
            for key in (
                "baseline:dev",
                f"{candidate_id}:dev",
                "baseline:qualification",
                f"{candidate_id}:qualification",
            )
        ):
            b_dev = measurements[("baseline", "dev")][1].value
            c_dev = measurements[(candidate_id, "dev")][1].value
            b_qual = measurements[("baseline", "qualification")][1].value
            c_qual = measurements[(candidate_id, "qualification")][1].value
            dev_gain = b_dev - c_dev if manifest.metric.direction == "min" else c_dev - b_dev
            gain = b_qual - c_qual if manifest.metric.direction == "min" else c_qual - b_qual
            policy = manifest.qualification_policy
            strict = not policy.allow_equal
            dev_ok = dev_gain > 0 if strict else dev_gain >= 0
            qual_ok = gain > 0 if strict else gain >= 0
            if not dev_ok:
                status, reason = "REJECTED", "DEVELOPMENT_NOT_IMPROVED"
            elif not qual_ok or gain < policy.minimum_absolute_improvement:
                status, reason = "REJECTED", "QUALIFICATION_THRESHOLD_NOT_MET"
            else:
                status, reason = "QUALIFIED", "PRESPECIFIED_GATE_MET"
        return {
            "candidate_id": candidate_id,
            "status": status,
            "reason": reason,
            "policy_version": manifest.qualification_policy.name,
            "policy_sha256": frozen.policy_sha256,
            "qualified_improvement": gain,
            "minimum_absolute_improvement": (
                manifest.qualification_policy.minimum_absolute_improvement
            ),
            "evidence_events": referenced,
            "qualification_attempt_exposure": exposure,
            "selection_stage_only": True,
        }

    def qualify(self, candidate_id: str) -> dict[str, Any]:
        with self.runner._lease():
            _, frozen, _, _, ledger = self.runner._ready()
            result = self.decide(candidate_id)
            prior: tuple[Any, dict[str, Any]] | None = None
            for event, payload in self._payloads(ledger, frozen.campaign_id):
                if event.event_type == "QUALIFICATION_DECIDED" and event.subject_id == candidate_id:
                    prior = (event, payload)
            if prior is not None and prior[1].get("evidence_events") == result["evidence_events"]:
                # Event-chain integrity does not imply a correct policy verdict.
                fields = (
                    "candidate_id", "status", "reason", "policy_version",
                    "policy_sha256", "qualified_improvement",
                    "minimum_absolute_improvement", "selection_stage_only",
                )
                if any(prior[1].get(key) != result[key] for key in fields):
                    raise RunnerIntegrityError(
                        "Stored qualification decision contradicts deterministic replay"
                    )
                old_exposure = prior[1].get("qualification_attempt_exposure")
                if (
                    not isinstance(old_exposure, int)
                    or not 0 <= old_exposure <= result["qualification_attempt_exposure"]
                ):
                    raise RunnerIntegrityError("Invalid historical qualification exposure")
                return prior[1]
            result["supersedes_event_sha256"] = prior[0].event_sha256 if prior else None
            self._append(ledger, frozen.campaign_id, "QUALIFICATION_DECIDED", candidate_id, result)
            return result

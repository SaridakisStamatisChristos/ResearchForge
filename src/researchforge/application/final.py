"""One-shot final plan, exact-evidence replay and bounded final conclusions (PR06).

Final data may be read only after a qualified candidate and comparison plan are locked.
One started final evaluation consumes the one-shot right even if its process crashes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from researchforge.application.evidence import EventLedger
from researchforge.application.qualification import QualificationService
from researchforge.application.runner import (
    TERMINAL,
    ExecutionRefused,
    RunnerIntegrityError,
    RunnerService,
    _result,
)
from researchforge.domain.models import ArtifactRef


class FinalService:
    """No final test or evaluator subprocess is consulted during candidate selection."""

    def __init__(self, path: Path) -> None:
        self.runner = RunnerService(path)
        self.selection = QualificationService(path)

    @staticmethod
    def _entries(ledger: EventLedger, campaign_id: str) -> list[tuple[Any, dict[str, Any]]]:
        return QualificationService._payloads(ledger, campaign_id)

    @staticmethod
    def _event(
        ledger: EventLedger, campaign: str, kind: str, ident: str, payload: dict[str, Any]
    ) -> Any:
        return QualificationService._append(ledger, campaign, kind, ident, payload)

    def _frozen_plan(
        self, ledger: EventLedger, campaign_id: str, frozen: Any
    ) -> tuple[Any, dict[str, Any]] | None:
        plans = [
            (event, payload)
            for event, payload in self._entries(ledger, campaign_id)
            if event.event_type == "FINAL_PLAN_FROZEN"
        ]
        if len(plans) > 1:
            raise RunnerIntegrityError("Multiple final plans are forbidden")
        if not plans:
            return None
        event, plan = plans[0]
        if (
            event.subject_id != plan.get("candidate_id")
            or plan.get("manifest_sha256") != frozen.manifest_sha256
            or plan.get("policy_sha256") != frozen.policy_sha256
            or plan.get("split_sha256") != frozen.splits["final"].sha256
            or plan.get("baseline_commit") != frozen.repository_commit
            or plan.get("baseline_tree") != frozen.repository_tree
            or plan.get("method") != "aggregate-strict-improvement-v1"
            or plan.get("arms") != ["baseline", plan.get("candidate_id")]
        ):
            raise RunnerIntegrityError("Frozen final plan does not match locked campaign")
        return event, plan

    def nominate(self, candidate_id: str) -> dict[str, Any]:
        """Seal one qualified candidate and the final comparison before data exposure."""
        with self.runner._lease():
            manifest, frozen, _, _, ledger = self.runner._ready()
            if self.runner._open_attempts(ledger, frozen.campaign_id):
                raise ExecutionRefused("Unresolved attempt prevents final nomination")
            existing = self._frozen_plan(ledger, frozen.campaign_id, frozen)
            if existing is not None:
                if existing[1]["candidate_id"] != candidate_id:
                    raise ExecutionRefused("Final candidate is immutable after nomination")
                self._check_qualified(ledger, frozen.campaign_id, candidate_id, existing[1])
                return existing[1]
            _, candidates = self.selection.registry(ledger, frozen.campaign_id)
            if candidate_id not in candidates:
                raise ExecutionRefused("Candidate not registered")
            checked = self.selection.decide(candidate_id)
            if checked["status"] != "QUALIFIED":
                raise ExecutionRefused("Only a prequalified candidate can be nominated")
            decisions = [
                (event, payload)
                for event, payload in self._entries(ledger, frozen.campaign_id)
                if event.event_type == "QUALIFICATION_DECIDED" and event.subject_id == candidate_id
            ]
            if not decisions:
                raise ExecutionRefused("Record qualification decision before nomination")
            selection_event, saved = decisions[-1]
            self._compare_decision(checked, saved)
            candidate = candidates[candidate_id]
            plan = {
                "candidate_id": candidate_id,
                "candidate_commit": candidate["commit"],
                "candidate_tree": candidate["tree"],
                "baseline_commit": frozen.repository_commit,
                "baseline_tree": frozen.repository_tree,
                "manifest_sha256": frozen.manifest_sha256,
                "policy_sha256": frozen.policy_sha256,
                "split_sha256": frozen.splits["final"].sha256,
                "selection_event_sha256": selection_event.event_sha256,
                "qualification_attempt_exposure": checked["qualification_attempt_exposure"],
                "metric": manifest.metric.name,
                "direction": manifest.metric.direction,
                "threshold": manifest.qualification_policy.minimum_absolute_improvement,
                "allow_equal": manifest.qualification_policy.allow_equal,
                "method": "aggregate-strict-improvement-v1",
                "arms": ["baseline", candidate_id],
            }
            self._event(ledger, frozen.campaign_id, "FINAL_PLAN_FROZEN", candidate_id, plan)
            return plan

    @staticmethod
    def _compare_decision(computed: dict[str, Any], stored: dict[str, Any]) -> None:
        fields = (
            "candidate_id",
            "status",
            "reason",
            "policy_version",
            "policy_sha256",
            "qualified_improvement",
            "minimum_absolute_improvement",
            "evidence_events",
            "qualification_attempt_exposure",
            "selection_stage_only",
        )
        if any(computed[field] != stored.get(field) for field in fields):
            raise RunnerIntegrityError("Stored qualification verdict cannot be replayed")

    def _check_qualified(
        self, ledger: EventLedger, campaign_id: str, candidate_id: str, plan: dict[str, Any]
    ) -> None:
        computed = self.selection.decide(candidate_id)
        saved = [
            (event, payload)
            for event, payload in self._entries(ledger, campaign_id)
            if event.event_type == "QUALIFICATION_DECIDED" and event.subject_id == candidate_id
        ]
        if not saved:
            raise RunnerIntegrityError("Missing selected qualification decision")
        event, payload = saved[-1]
        self._compare_decision(computed, payload)
        if (
            computed["status"] != "QUALIFIED"
            or event.event_sha256 != plan["selection_event_sha256"]
            or computed["qualification_attempt_exposure"] != plan["qualification_attempt_exposure"]
        ):
            raise RunnerIntegrityError("Final plan not backed by qualified selection")

    def _final_records(
        self,
        ledger: EventLedger,
        campaign_id: str,
        frozen: Any,
        manifest: Any,
        plan_event: Any,
        plan: dict[str, Any],
    ) -> tuple[dict[str, tuple[Any, Any]], bool]:
        """Reconstruct final-arm evidence without trusting result values in the ledger."""
        starts: dict[str, tuple[Any, dict[str, Any]]] = {}
        terminal: dict[str, tuple[Any, Any]] = {}
        began = False
        ended = False
        for event, payload in self._entries(ledger, campaign_id):
            if event.event_type == "FINAL_EVALUATION_STARTED":
                if (
                    began
                    or event.sequence <= plan_event.sequence
                    or event.subject_id != plan["candidate_id"]
                ):
                    raise RunnerIntegrityError("Invalid or duplicate final start")
                if payload != {"plan_event_sha256": plan_event.event_sha256}:
                    raise RunnerIntegrityError("Unbound final start")
                began = True
            elif event.event_type == "FINAL_EVALUATED":
                if ended or not began:
                    raise RunnerIntegrityError("Unstarted or duplicate final conclusion")
                ended = True
            elif event.event_type == "ATTEMPT_STARTED" and payload.get("role") == "final":
                ident = payload.get("candidate_id")
                expected_commit = (
                    plan["baseline_commit"]
                    if ident == "baseline"
                    else plan["candidate_commit"]
                    if ident == plan["candidate_id"]
                    else None
                )
                expected_tree = (
                    plan["baseline_tree"]
                    if ident == "baseline"
                    else plan["candidate_tree"]
                    if ident == plan["candidate_id"]
                    else None
                )
                if (
                    not began
                    or ended
                    or event.subject_id in starts
                    or ident in {pair[1].get("candidate_id") for pair in starts.values()}
                    or len(starts) >= 2
                    or ident != plan["arms"][len(starts)]
                    or payload.get("commit") != expected_commit
                    or payload.get("tree") != expected_tree
                    or payload.get("split_sha256") != plan["split_sha256"]
                    or payload.get("evaluator_sha256") != frozen.evaluator_source_sha256
                    or payload.get("environment_sha256") != frozen.environment_sha256
                    or payload.get("explicit_approval") is not True
                    or payload.get("attempt_id") != event.subject_id
                ):
                    raise RunnerIntegrityError("Final attempt violates frozen plan")
                starts[event.subject_id] = (event, payload)
            elif event.event_type in TERMINAL and event.subject_id in starts:
                if event.subject_id in terminal:
                    raise RunnerIntegrityError("Duplicate terminal final attempt")
                start_event, start = starts[event.subject_id]
                ident = start["candidate_id"]
                if event.sequence <= start_event.sequence:
                    raise RunnerIntegrityError("Terminal final event precedes start")
                if event.event_type == "ATTEMPT_SUCCEEDED":
                    try:
                        output = ledger.cas.get(ArtifactRef.model_validate(payload["stdout_ref"]))
                        ledger.cas.get(ArtifactRef.model_validate(payload["stderr_ref"]))
                        decoded = _result(
                            output,
                            manifest=manifest,
                            expected_hash=frozen.splits["final"].sha256,
                            count=frozen.splits["final"].case_count,
                        )
                        if (
                            payload["status"] != "SUCCEEDED"
                            or payload["exit_code"] != 0
                            or payload["validated_result"] != decoded.model_dump(mode="json")
                            or ledger.cas.get(ArtifactRef.model_validate(payload["result_ref"]))
                            != output
                        ):
                            raise ValueError("Final output or validated result mismatch")
                        terminal[ident] = (event, decoded)
                    except (ValueError, KeyError, ValidationError, TypeError) as exc:
                        raise RunnerIntegrityError("Unverifiable final evaluator output") from exc
                else:
                    if payload.get("status") not in {"FAILED", "TIMED_OUT", "UNKNOWN", None}:
                        raise RunnerIntegrityError("Contradictory unsuccessful final status")
                    terminal[ident] = (event, None)
        if ended and (len(starts) != 2 or len(terminal) != 2):
            raise RunnerIntegrityError("Final conclusion lacks both terminal arms")
        if len(starts) != len(terminal):
            # An interrupted final run consumed the one-shot exposure.
            return terminal, began
        return terminal, began

    @staticmethod
    def _conclusion(records: dict[str, tuple[Any, Any]], plan: dict[str, Any]) -> dict[str, Any]:
        references = {name: event.event_sha256 for name, (event, _) in records.items()}
        candidate_id = str(plan["candidate_id"])
        baseline_row = records.get("baseline")
        candidate_row = records.get(candidate_id)
        status, reason = "FINAL_INCONCLUSIVE", "MISSING_VERIFIED_FINAL_EVIDENCE"
        gain: float | None = None
        if baseline_row and candidate_row and baseline_row[1] and candidate_row[1]:
            base = baseline_row[1].value
            candidate = candidate_row[1].value
            gain = base - candidate if plan["direction"] == "min" else candidate - base
            qualifies = gain >= plan["threshold"] and (gain > 0 or plan["allow_equal"])
            status = "FINAL_SUPPORTED" if qualifies else "FINAL_NOT_SUPPORTED"
            reason = (
                "PREDECLARED_AGGREGATE_THRESHOLD_MET" if qualifies else "FINAL_THRESHOLD_NOT_MET"
            )
        return {
            "candidate_id": candidate_id,
            "status": status,
            "reason": reason,
            "measured_improvement": gain,
            "threshold": plan["threshold"],
            "method": plan["method"],
            "final_evidence_events": references,
            "selection_event_sha256": plan["selection_event_sha256"],
            "qualification_attempt_exposure": plan["qualification_attempt_exposure"],
            "uncertainty": "NOT_ESTIMATED_FROM_AGGREGATE_ONLY",
            "limitations": [
                "Aggregate final difference does not establish population superiority",
                "No case-level paired interval is available from this evaluator contract",
                "Trusted-local subprocess execution is not a security sandbox",
            ],
        }

    def evaluate(self, *, approved: bool) -> dict[str, Any]:
        if not approved:
            raise ExecutionRefused("Explicit --approve-trusted-local is required")
        with self.runner._lease():
            manifest, frozen, _, _, ledger = self.runner._ready()
            item = self._frozen_plan(ledger, frozen.campaign_id, frozen)
            if item is None:
                raise ExecutionRefused("Freeze a final plan before execution")
            plan_event, plan = item
            self._check_qualified(ledger, frozen.campaign_id, plan["candidate_id"], plan)
            entries = self._entries(ledger, frozen.campaign_id)
            if any(
                event.event_type in {"FINAL_EVALUATION_STARTED", "FINAL_EVALUATED"}
                for event, _ in entries
            ):
                raise ExecutionRefused("Final evaluation is one-shot and cannot be restarted")
            if self.runner._open_attempts(ledger, frozen.campaign_id):
                raise ExecutionRefused("Unresolved attempt prevents final evaluation")
            count = sum(event.event_type == "ATTEMPT_STARTED" for event, _ in entries)
            if count + 2 > manifest.budget.max_attempts:
                raise ExecutionRefused("Insufficient reserved evaluation-attempt budget")
            self._event(
                ledger,
                frozen.campaign_id,
                "FINAL_EVALUATION_STARTED",
                plan["candidate_id"],
                {"plan_event_sha256": plan_event.event_sha256},
            )
            # A crash after this event consumes final exposure. Never automatically rerun.
            for identity in plan["arms"]:
                self.runner._run_locked(
                    role="final",
                    candidate_id=None if identity == "baseline" else identity,
                    allow_final=True,
                )
            records, began = self._final_records(
                ledger, frozen.campaign_id, frozen, manifest, plan_event, plan
            )
            if not began:
                raise RunnerIntegrityError("Final execution is incomplete or interrupted")
            summary = self._conclusion(records, plan)
            self._event(
                ledger, frozen.campaign_id, "FINAL_EVALUATED", plan["candidate_id"], summary
            )
            return summary

    def inspect(self) -> dict[str, Any]:
        """Read-only replay of selection, final plan and final conclusion."""
        manifest, frozen, _, _, ledger = self.runner._ready()
        state = ledger.verify(frozen.campaign_id)
        item = self._frozen_plan(ledger, frozen.campaign_id, frozen)
        if item is None:
            return {
                "campaign_id": frozen.campaign_id,
                "ledger": state,
                "final_status": "NOT_NOMINATED",
                "final_plan": None,
                "final_result": None,
            }
        plan_event, plan = item
        self._check_qualified(ledger, frozen.campaign_id, plan["candidate_id"], plan)
        records, started = self._final_records(
            ledger, frozen.campaign_id, frozen, manifest, plan_event, plan
        )
        conclusion = self._conclusion(records, plan)
        finishes = [
            (event, payload)
            for event, payload in self._entries(ledger, frozen.campaign_id)
            if event.event_type == "FINAL_EVALUATED"
        ]
        if len(finishes) > 1:
            raise RunnerIntegrityError("Multiple final conclusions")
        if finishes:
            if not started or finishes[0][1] != conclusion:
                raise RunnerIntegrityError("Final conclusion contradicts deterministic replay")
            return {
                "campaign_id": frozen.campaign_id,
                "ledger": state,
                "final_status": conclusion["status"],
                "final_plan": plan,
                "final_result": conclusion,
            }
        return {
            "campaign_id": frozen.campaign_id,
            "ledger": state,
            "final_status": "FINAL_UNKNOWN" if started else "NOMINATED",
            "final_plan": plan,
            "final_result": None,
            "final_attempt_events": {key: val[0].event_sha256 for key, val in records.items()},
        }

    def report_markdown(self) -> str:
        """Generate a report solely from the audited local event chain and CAS blobs."""
        data = self.inspect()
        report = [
            "# ResearchForge verified campaign report",
            "",
            f"Campaign: `{data['campaign_id']}`",
            f"Final status: **{data['final_status']}**",
            f"Ledger head: `{data['ledger']['chain_head']}`",
            "",
        ]
        plan = data["final_plan"]
        if plan is not None:
            report.extend(
                [
                    "## Frozen final plan",
                    f"Candidate: `{plan['candidate_id']}`",
                    f"Commit: `{plan['candidate_commit']}`",
                    f"Selected qualification event: `{plan['selection_event_sha256']}`",
                    f"Selection-stage qualification exposure: {plan['qualification_attempt_exposure']}",
                    "",
                ]
            )
        final = data["final_result"]
        if final is not None:
            report.extend(
                [
                    "## Final aggregate comparison",
                    f"Difference (positive favors candidate): {final['measured_improvement']}",
                    f"Predeclared minimum improvement: {final['threshold']}",
                    f"Basis: {final['reason']}",
                    "Final evidence event hashes:",
                ]
            )
            for name, evidence in sorted(final["final_evidence_events"].items()):
                report.append(f"- {name}: `{evidence}`")
            report.extend(["", "## Limitations"])
            for limitation in final["limitations"]:
                report.append(f"- {limitation}")
        else:
            report.extend(["## Limitations", "- No confirmed final aggregate result available."])
        return "\n".join(report) + "\n"

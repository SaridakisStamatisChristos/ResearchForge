"""PR14: predeclared, paired agent-workflow experiment with independently replayed answers.

Scores describe the sampled tasks, NOT scientific or model uplift in a population.
Trusted subprocesses are not a sandbox. Local hashes are not signed attestation.
"""

from __future__ import annotations

import json
import math
import os
import random
import re
from pathlib import Path
from typing import Annotated, Any, Literal

import typer
from pydantic import Field, ValidationError, model_validator

from researchforge.application.runner import _bounded_process
from researchforge.case_studies import _parse_json, _safe_file, _write_new
from researchforge.domain.models import StrictModel
from researchforge.domain.serialization import canonical_json, digest_bytes
from researchforge.statistics import paired_comparison

_ID = r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,55}$"
_SHA = r"^[0-9a-f]{64}$"
_TOKENS = {"{task}", "{output}", "{context}", "{seed}"}
_MAX_FILE = 1_048_576


class UpliftError(ValueError):
    """Invalid, incomplete, or tampered uplift evidence."""


class UpliftRefused(UpliftError):
    """Trusted execution was not authorized or the executable drifted."""


class Task(StrictModel):
    task_id: str = Field(pattern=_ID)
    family: str = Field(pattern=_ID)
    prompt: str = Field(min_length=1, max_length=5000)
    expected_answer: str = Field(max_length=2000)


class Skill(StrictModel):
    path: str = Field(min_length=1, max_length=250)
    sha256: str = Field(pattern=_SHA)

    @model_validator(mode="after")
    def safe_path(self) -> Skill:
        parts = Path(self.path)
        if parts.is_absolute() or any(p in ("", ".", "..") for p in parts.parts):
            raise ValueError("Skill path must be safe and relative to the suite")
        if not re.fullmatch(r"[a-zA-Z0-9._/-]+", self.path):
            raise ValueError("Unsupported skill path characters")
        return self


class Arm(StrictModel):
    name: str = Field(pattern=_ID)
    condition: Literal["control", "full", "ablation"]
    components: list[str] = Field(default_factory=list, max_length=12)
    argv: list[str] = Field(min_length=3, max_length=40)
    executable_sha256: str = Field(pattern=_SHA)
    pinned_files: dict[str, str] = Field(default_factory=dict, max_length=12)
    agent_identity: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def valid_command(self) -> Arm:
        if (
            not Path(self.argv[0]).is_absolute()
            or self.argv.count("{task}") != 1
            or self.argv.count("{output}") != 1
            or self.argv.count("{context}") != 1
            or any(
                not item or "\x00" in item or (("{" in item or "}" in item) and item not in _TOKENS)
                for item in self.argv
            )
        ):
            raise ValueError(
                "An absolute executable and separate task/output/context tokens required"
            )
        if len(set(self.components)) != len(self.components):
            raise ValueError("Duplicate components")
        for path, sha in self.pinned_files.items():
            if not Path(path).is_absolute() or path not in self.argv or not re.fullmatch(_SHA, sha):
                raise ValueError("Pinned auxiliary file must be an exact absolute argv element")
        return self


class UpliftPlan(StrictModel):
    schema_version: Literal["1.0"]
    study_id: str = Field(pattern=_ID)
    provenance_class: Literal["EXTERNAL_AGENT", "MECHANICAL_FIXTURE"]
    design_note: str = Field(min_length=10, max_length=2000)
    task_sampling: str = Field(min_length=3, max_length=500)
    seed: int = Field(ge=0, le=2_147_483_647)
    bootstrap_resamples: int = Field(ge=200, le=10000)
    timeout_seconds: int = Field(ge=1, le=3600)
    max_output_bytes: int = Field(ge=256, le=_MAX_FILE)
    max_total_wall_seconds: int = Field(ge=1, le=86400)
    allowed_environment_keys: list[str] = Field(default_factory=list, max_length=20)
    tasks: list[Task] = Field(min_length=2, max_length=80)
    skills: dict[str, Skill] = Field(min_length=1, max_length=12)
    arms: list[Arm] = Field(min_length=3, max_length=14)

    @model_validator(mode="after")
    def valid_design(self) -> UpliftPlan:
        if len(set(t.task_id for t in self.tasks)) != len(self.tasks):
            raise ValueError("Duplicate task identities")
        if len(self.tasks) * len(self.arms) > 240:
            raise ValueError("Exceeded experiment trial cap")
        if len(set(a.name for a in self.arms)) != len(self.arms):
            raise ValueError("Duplicate arm identities")
        if len({a.agent_identity for a in self.arms}) != 1:
            raise ValueError("All conditions must have the same declared agent/model identity")
        signatures = {
            (tuple(a.argv), a.executable_sha256, tuple(sorted(a.pinned_files.items())))
            for a in self.arms
        }
        if len(signatures) != 1:
            raise ValueError("All conditions must use the same pinned executable and argv")
        if len(set(self.allowed_environment_keys)) != len(self.allowed_environment_keys):
            raise ValueError("Duplicate environment names")
        if any(not re.fullmatch(r"[A-Z_][A-Z0-9_]*", k) for k in self.allowed_environment_keys):
            raise ValueError("Invalid environment key")
        controls = [a for a in self.arms if a.condition == "control"]
        fulls = [a for a in self.arms if a.condition == "full"]
        if len(controls) != 1 or len(fulls) != 1:
            raise ValueError("Exactly one control and one full arm required")
        names = set(self.skills)
        if any(not re.fullmatch(_ID, name) for name in names):
            raise ValueError("Invalid skill component name")
        if controls[0].components or set(fulls[0].components) != names:
            raise ValueError("Control must receive no skill; full arm must receive all skills")
        ablations = [a for a in self.arms if a.condition == "ablation"]
        if not ablations or len(ablations) != len(names):
            raise ValueError("Exactly one leave-one-component-out ablation per skill required")
        removed: set[str] = set()
        for arm in ablations:
            missing = names - set(arm.components)
            if len(missing) != 1 or set(arm.components) - names:
                raise ValueError("Ablation must remove exactly one skill")
            removed.update(missing)
        if removed != names or len({frozenset(a.components) for a in ablations}) != len(names):
            raise ValueError("Ablation design duplicates or omits a component")
        return self


def read_uplift_plan(path: Path) -> UpliftPlan:
    if path.is_symlink() or path.stat().st_size > _MAX_FILE:
        raise UpliftError("Missing or oversized plan")
    try:
        return UpliftPlan.model_validate(_parse_json(path.read_bytes()))
    except (ValueError, UnicodeError, ValidationError) as exc:
        raise UpliftError("Invalid, duplicate-key or non-finite plan") from exc


def _source_skill(root: Path, skill: Skill) -> bytes:
    contents = _safe_file(root, skill.path, maximum=_MAX_FILE)
    if digest_bytes(contents) != skill.sha256:
        raise UpliftRefused("Skill source has changed since preregistration")
    return contents


def _pin(arm: Arm) -> None:
    paths = {arm.argv[0]: arm.executable_sha256, **arm.pinned_files}
    for filename, sha in paths.items():
        file = Path(filename)
        if not file.is_file() or file.is_symlink() or file.stat().st_size > 100_000_000:
            raise UpliftRefused("Missing or unsafe pinned executable/source")
        if digest_bytes(file.read_bytes()) != sha:
            raise UpliftRefused("Pinned executable/source SHA-256 drift")


def _answer(data: bytes, task: Task, code: int | None, failure: str | None) -> tuple[str, str]:
    if failure is not None:
        return "UNKNOWN", failure
    if code != 0:
        return "FAILED", "NONZERO_EXIT"
    if len(data) > _MAX_FILE or not data:
        return "UNKNOWN", "MISSING_OR_OVERSIZED_RESPONSE"
    try:
        response = _parse_json(data)
        if not isinstance(response, dict) or set(response) != {"answer"}:
            return "UNKNOWN", "INVALID_RESPONSE_SCHEMA"
        if not isinstance(response["answer"], str):
            return "UNKNOWN", "INVALID_ANSWER_TYPE"
    except (ValueError, UnicodeError):
        return "UNKNOWN", "INVALID_RESPONSE_JSON"
    if response["answer"] == task.expected_answer:
        return "PASS", "INDEPENDENT_EXACT_ANSWER"
    return "FAIL", "INDEPENDENT_EXACT_ANSWER"


def _score(status: str) -> float | None:
    if status == "PASS":
        return 1.0
    if status in {"FAIL", "FAILED"}:
        return 0.0
    return None


def _trial_paths(task: Task, arm: Arm) -> str:
    return f"trials/{task.task_id}/{arm.name}"


def _argv(arm: Arm, task_file: Path, output: Path, context: Path, seed: int) -> list[str]:
    values = {
        "{task}": str(task_file),
        "{output}": str(output),
        "{context}": str(context),
        "{seed}": str(seed),
    }
    return [values.get(piece, piece) for piece in arm.argv]


def run_uplift(plan_file: Path, bundle: Path, *, approved: bool) -> dict[str, Any]:
    """Execute shell-free real agent commands; no score is imported or self-reported."""
    if not approved:
        raise UpliftRefused("--approve-trusted-agent required; this is not a sandbox")
    plan = read_uplift_plan(plan_file)
    source = plan_file.parent
    skills = {name: _source_skill(source, spec) for name, spec in plan.skills.items()}
    for arm in plan.arms:
        _pin(arm)
    bundle.mkdir(parents=True, exist_ok=False)
    plan_bytes = canonical_json(plan.model_dump(mode="json"))
    _write_new(bundle, "plan.json", plan_bytes)
    for name, raw in skills.items():
        _write_new(bundle, f"skills/{name}.md", raw)

    rows: list[dict[str, Any]] = []
    total_wall = 0.0
    # Randomize condition order independently inside each fixed paired task.
    rng = random.Random(plan.seed)
    for task in plan.tasks:
        for arm in rng.sample(plan.arms, k=len(plan.arms)):
            if plan.max_total_wall_seconds - total_wall < 1:
                raise UpliftRefused(
                    "Total measured wall budget exhausted; partial bundle unverified"
                )
            _pin(arm)
            prefix = _trial_paths(task, arm)
            task_bytes = canonical_json({"task_id": task.task_id, "prompt": task.prompt})
            _write_new(bundle, f"{prefix}/task.json", task_bytes)
            context = bundle / prefix / "context"
            context.mkdir(parents=True)
            for name in arm.components:
                _write_new(bundle, f"{prefix}/context/{name}.md", skills[name])
            response_path = bundle / prefix / "answer.json"
            argv = _argv(
                arm,
                (bundle / prefix / "task.json").resolve(),
                response_path.resolve(),
                context.resolve(),
                plan.seed,
            )
            budget = min(
                plan.timeout_seconds,
                max(1, math.floor(plan.max_total_wall_seconds - total_wall)),
            )
            code, stdout, stderr, failure, seconds = _bounded_process(
                argv,
                cwd=(bundle / prefix).resolve(),
                timeout=budget,
                max_output=plan.max_output_bytes,
                env={
                    k: value
                    for k, value in os.environ.items()
                    if k in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR"}
                    or k in plan.allowed_environment_keys
                },
            )
            if not math.isfinite(seconds) or seconds < 0:
                raise UpliftError("Nonfinite observed process duration")
            total_wall += seconds
            if total_wall > plan.max_total_wall_seconds:
                raise UpliftRefused("Experiment exhausted the cumulative wall budget")
            _write_new(bundle, f"{prefix}/stdout.log", stdout)
            _write_new(bundle, f"{prefix}/stderr.log", stderr)
            present = response_path.is_file() and not response_path.is_symlink()
            raw = (
                response_path.read_bytes()
                if present and response_path.stat().st_size <= _MAX_FILE
                else b""
            )
            if present and response_path.stat().st_size > _MAX_FILE:
                failure = "OUTPUT_LIMIT"
            _write_new(bundle, f"{prefix}/captured_answer.json", raw)
            status, reason = _answer(raw, task, code, failure)
            rows.append(
                {
                    "task_id": task.task_id,
                    "arm": arm.name,
                    "argv": argv,
                    "exit_code": code,
                    "failure": failure,
                    "elapsed_seconds": seconds,
                    "answer_present": present,
                    "answer_sha256": digest_bytes(raw),
                    "stdout_sha256": digest_bytes(stdout),
                    "stderr_sha256": digest_bytes(stderr),
                    "task_sha256": digest_bytes(task_bytes),
                    "status": status,
                    "reason": reason,
                }
            )

    receipt = {
        "schema_version": "1.0",
        "provenance": "EXECUTED_LOCAL_COMMANDS",
        "execution_root": str(bundle.resolve()),
        "plan_sha256": digest_bytes(plan_bytes),
        "rows": rows,
    }
    raw_receipt = canonical_json(receipt)
    _write_new(bundle, "receipt.json", raw_receipt)
    _write_new(bundle, "receipt.sha256", (digest_bytes(raw_receipt) + "\n").encode("ascii"))
    report = verify_uplift(bundle)
    _write_new(bundle, "report.json", canonical_json(report))
    _write_new(bundle, "report.md", render_uplift(report).encode("utf-8"))
    return report


def _comparison(
    plan: UpliftPlan,
    observations: dict[str, dict[str, float | None]],
    left: str,
    right: str,
) -> dict[str, Any]:
    lhs = observations[left]
    rhs = observations[right]
    complete = all(lhs[t.task_id] is not None and rhs[t.task_id] is not None for t in plan.tasks)
    pairs = [(lhs[t.task_id], rhs[t.task_id]) for t in plan.tasks]
    left_pass = sum(x == 1 for x, _ in pairs)
    right_pass = sum(y == 1 for _, y in pairs)
    result: dict[str, Any] = {
        "control_arm": left,
        "treatment_arm": right,
        "tasks": len(plan.tasks),
        "known_control_passes": left_pass,
        "known_treatment_passes": right_pass,
        "unknown_pairs": sum(x is None or y is None for x, y in pairs),
        "status": "DESCRIPTIVE_ONLY" if complete else "INCONCLUSIVE",
        "delta_pass_rate": (right_pass - left_pass) / len(plan.tasks) if complete else None,
        "uncertainty": None,
    }
    if complete:
        result["uncertainty"] = paired_comparison(
            {key: float(value) for key, value in lhs.items() if value is not None},
            {key: float(value) for key, value in rhs.items() if value is not None},
            direction="max",
            seed=plan.seed,
            resamples=plan.bootstrap_resamples,
        )
    return result


def verify_uplift(bundle: Path) -> dict[str, Any]:
    """Offline replay. Compares original frozen tasks to raw response bytes, not reported scores."""
    plan_data = _safe_file(bundle, "plan.json")
    try:
        plan = UpliftPlan.model_validate(_parse_json(plan_data))
        raw = _safe_file(bundle, "receipt.json")
        supplied = _safe_file(bundle, "receipt.sha256").decode("ascii").strip()
        if digest_bytes(raw) != supplied or canonical_json(_parse_json(raw)) != raw:
            raise UpliftError("Receipt digest/canonical encoding disagreement")
        receipt = _parse_json(raw)
        if (
            not isinstance(receipt, dict)
            or set(receipt)
            != {"schema_version", "provenance", "execution_root", "plan_sha256", "rows"}
            or receipt["schema_version"] != "1.0"
            or receipt["provenance"] != "EXECUTED_LOCAL_COMMANDS"
            or receipt["plan_sha256"] != digest_bytes(plan_data)
            or not isinstance(receipt["execution_root"], str)
            or not Path(receipt["execution_root"]).is_absolute()
            or not isinstance(receipt["rows"], list)
            or len(receipt["rows"]) != len(plan.tasks) * len(plan.arms)
            or canonical_json(plan.model_dump(mode="json")) != plan_data
        ):
            raise UpliftError("Invalid evidence receipt")
        for name, spec in plan.skills.items():
            if digest_bytes(_safe_file(bundle, f"skills/{name}.md")) != spec.sha256:
                raise UpliftError("Archived skill bytes changed")
        expected: set[tuple[str, str]] = set()
        observed: dict[str, dict[str, float | None]] = {a.name: {} for a in plan.arms}
        statuses: dict[str, dict[str, str]] = {a.name: {} for a in plan.arms}
        task_by_id = {t.task_id: t for t in plan.tasks}
        arm_by_name = {a.name: a for a in plan.arms}
        for row in receipt["rows"]:
            if not isinstance(row, dict):
                raise UpliftError("Invalid trial receipt")
            tid, aid = row.get("task_id"), row.get("arm")
            if not isinstance(tid, str) or not isinstance(aid, str):
                raise UpliftError("Missing pair identities")
            key = (tid, aid)
            if key in expected or tid not in task_by_id or aid not in arm_by_name:
                raise UpliftError("Duplicate or unexpected task/arm pair")
            expected.add(key)
            task, arm = task_by_id[tid], arm_by_name[aid]
            prefix = _trial_paths(task, arm)
            task_raw = _safe_file(bundle, f"{prefix}/task.json")
            if task_raw != canonical_json({"task_id": task.task_id, "prompt": task.prompt}):
                raise UpliftError("Task instructions changed")
            for name in arm.components:
                if _safe_file(bundle, f"{prefix}/context/{name}.md") != _safe_file(
                    bundle, f"skills/{name}.md"
                ):
                    raise UpliftError("Delivered skill context changed")
            root = Path(receipt["execution_root"]) / prefix
            expected_argv = _argv(
                arm, root / "task.json", root / "answer.json", root / "context", plan.seed
            )
            if row.get("argv") != expected_argv or row.get("task_sha256") != digest_bytes(task_raw):
                raise UpliftError("Executed arguments do not match frozen task/arm")
            data = _safe_file(bundle, f"{prefix}/captured_answer.json")
            stdout = _safe_file(bundle, f"{prefix}/stdout.log")
            stderr = _safe_file(bundle, f"{prefix}/stderr.log")
            if (
                row.get("answer_sha256") != digest_bytes(data)
                or row.get("stdout_sha256") != digest_bytes(stdout)
                or row.get("stderr_sha256") != digest_bytes(stderr)
            ):
                raise UpliftError("Raw evidence hash mismatch")
            duration = row.get("elapsed_seconds")
            if (
                isinstance(duration, bool)
                or not isinstance(duration, (int, float))
                or not math.isfinite(duration)
                or duration < 0
            ):
                raise UpliftError("Invalid wall clock")
            code = row.get("exit_code")
            if code is not None and type(code) is not int:
                raise UpliftError("Invalid exit code")
            failure = row.get("failure")
            if failure is not None and failure not in {"TIMEOUT", "OUTPUT_LIMIT", "UNKNOWN"}:
                raise UpliftError("Unknown failure state")
            if type(row.get("answer_present")) is not bool:
                raise UpliftError("Invalid response presence flag")
            if not row["answer_present"] and data:
                raise UpliftError("Absent agent output cannot have response bytes")
            state, reason = _answer(data, task, code, failure)
            if (row.get("status"), row.get("reason")) != (state, reason):
                raise UpliftError("Receipt score disagrees with independent oracle")
            observed[aid][tid] = _score(state)
            statuses[aid][tid] = state
        if len(expected) != len(plan.tasks) * len(plan.arms):
            raise UpliftError("Missing paired trials")
        control = next(a.name for a in plan.arms if a.condition == "control")
        full = next(a.name for a in plan.arms if a.condition == "full")
        comparisons = [_comparison(plan, observed, control, full)]
        for ablation in (a for a in plan.arms if a.condition == "ablation"):
            comparisons.append(_comparison(plan, observed, ablation.name, full))
        negative = [
            f"{x['control_arm']}→{x['treatment_arm']}: no positive observed gain"
            for x in comparisons
            if x["delta_pass_rate"] is not None and x["delta_pass_rate"] <= 0
        ]
        if any(x["status"] == "INCONCLUSIVE" for x in comparisons):
            negative.append("Unknown trials prohibit a complete paired conclusion")
        return {
            "schema_version": "1.0",
            "study_id": plan.study_id,
            "source": "OFFLINE_REPLAY_OF_LOCAL_COMMAND_RECEIPTS",
            "provenance_class": plan.provenance_class,
            "receipt_sha256": supplied,
            "plan_sha256": digest_bytes(plan_data),
            "task_count": len(plan.tasks),
            "agent_identity_declared": plan.arms[0].agent_identity,
            "arm_statuses": statuses,
            "comparisons": comparisons,
            "negative_or_null_findings": negative,
            "population_uplift_established": False,
            "limitations": [
                "Fixed-task bootstrap intervals do not demonstrate population uplift",
                "Agent identity, tokens and model settings are not independently attested",
                "Working-directory separation is not an adversarial security sandbox",
                "Local hashes cannot prevent coordinated forgery by an administrator",
                "Agent skill exposure does not prove that the model used a component",
                "Provider state, seeds and cross-task model memory may confound",
                "A mechanical fixture is not evidence of actual LLM/agent uplift",
            ],
        }
    except (OSError, UnicodeError, ValueError, ValidationError, TypeError) as exc:
        if isinstance(exc, UpliftError):
            raise
        raise UpliftError("Missing, incomplete or corrupt paired agent evidence") from exc


def render_uplift(report: dict[str, Any]) -> str:
    lines = [
        "# ResearchForge PR14 paired agent study",
        "",
        f"Study: {report['study_id']}; evidence: {report['provenance_class']}",
        f"Fixed tasks: {report['task_count']}; population uplift established: **NO**",
        "",
        "| Control | Treatment | Δ pass rate | Status |",
        "|---|---|---:|---|",
    ]
    for comp in report["comparisons"]:
        diff = comp["delta_pass_rate"]
        value = "UNKNOWN" if diff is None else f"{100 * diff:+.1f} pp"
        lines.append(
            f"| {comp['control_arm']} | {comp['treatment_arm']} | {value} | {comp['status']} |"
        )
    lines.extend(["", "## Negative, null and missing results"])
    lines.extend(f"- {finding}" for finding in report["negative_or_null_findings"])
    if not report["negative_or_null_findings"]:
        lines.append("- No nonpositive observed differences; this is not a generalization claim.")
    lines.extend(["", "## Limitations"])
    lines.extend(f"- {issue}" for issue in report["limitations"])
    return "\n".join(lines) + "\n"


uplift_commands = typer.Typer(help="Paired fixed-task agent study, ablations and offline replay.")


@uplift_commands.command("run")
def uplift_run_cli(
    plan: Annotated[Path, typer.Option("--plan")],
    output: Annotated[Path, typer.Option("--output")],
    approve: Annotated[bool, typer.Option("--approve-trusted-agent")] = False,
) -> None:
    """Run pinned agent commands only after operator consent; no shell or security sandbox."""
    try:
        report = run_uplift(plan, output, approved=approve)
        typer.echo(json.dumps(report, sort_keys=True))
    except UpliftRefused as exc:
        typer.echo(f"REFUSED: {exc}", err=True)
        raise typer.Exit(code=3) from exc
    except (UpliftError, ValueError, OSError, ValidationError) as exc:
        typer.echo(f"INVALID_OR_INCOMPLETE: {exc}", err=True)
        raise typer.Exit(code=5) from exc


@uplift_commands.command("verify")
def uplift_verify_cli(bundle: Annotated[Path, typer.Option("--bundle")]) -> None:
    """Independently rescore archived outputs; never spawn an agent."""
    try:
        typer.echo(json.dumps(verify_uplift(bundle), sort_keys=True))
    except (UpliftError, OSError) as exc:
        typer.echo(f"CORRUPT: {exc}", err=True)
        raise typer.Exit(code=6) from exc


@uplift_commands.command("report")
def uplift_report_cli(
    bundle: Annotated[Path, typer.Option("--bundle")],
    format: Annotated[Literal["json", "md"], typer.Option("--format")] = "md",
) -> None:
    """Read-only regenerated report; recorded report files are not trusted."""
    try:
        data = verify_uplift(bundle)
        typer.echo(render_uplift(data) if format == "md" else json.dumps(data, sort_keys=True))
    except (UpliftError, OSError) as exc:
        typer.echo(f"CORRUPT: {exc}", err=True)
        raise typer.Exit(code=6) from exc

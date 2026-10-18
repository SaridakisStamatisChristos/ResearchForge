"""PR13: trusted-local paired case studies and replayable raw-log receipts.

A real subprocess creates each log. Replay validates hashes and correctness,
but a locally edited bundle is not externally trusted execution attestation.
"""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Annotated, Any, Literal

import typer
from pydantic import Field, ValidationError, model_validator

from researchforge.application.runner import _bounded_process
from researchforge.case_oracles import CaseInputError, oracle, parse_dimacs
from researchforge.domain.models import StrictModel
from researchforge.domain.serialization import canonical_json, digest_bytes
from researchforge.statistics import paired_comparison

MAX_PLAN = 1_048_576
MAX_CASES = 200
MAX_CUTOFF = 3600
MAX_OUTPUT = 1_048_576
_CASE_ID = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$"
_RELPATH = re.compile(r"^[A-Za-z0-9._/-]{1,250}$")


class CaseBundleError(ValueError):
    """Missing/corrupt bundle, forbidden paths, or contradictory raw evidence."""


class CaseExecutionRefused(ValueError):
    """Unapproved or otherwise disallowed trusted-local execution."""


class ToolSpec(StrictModel):
    name: str = Field(pattern=_CASE_ID)
    argv: list[str] = Field(min_length=2, max_length=32)
    binary_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    version_or_commit: str = Field(min_length=1, max_length=160)
    pinned_files: dict[str, str] = Field(default_factory=dict, max_length=8)

    @model_validator(mode="after")
    def checked_argv(self) -> ToolSpec:
        if (
            not Path(self.argv[0]).is_absolute()
            or self.argv.count("{input}") != 1
            or any(
                not word
                or "\x00" in word
                or re.search(r"\{[^{}]*\}", word)
                and word not in {"{input}", "{seed}"}
                for word in self.argv
            )
        ):
            raise ValueError("Tool needs absolute executable and one {input}, no unknown tokens")
        for pinned_path, expected_sha in self.pinned_files.items():
            if (
                not Path(pinned_path).is_absolute()
                or pinned_path not in self.argv
                or not re.fullmatch(r"[a-f0-9]{64}", expected_sha)
            ):
                raise ValueError("Each pinned auxiliary source must be an absolute argv file")
        return self


class CaseSpec(StrictModel):
    case_id: str = Field(pattern=_CASE_ID)
    family: str = Field(pattern=_CASE_ID)
    input_path: str = Field(min_length=1)
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def valid_path(self) -> CaseSpec:
        _validate_relative(self.input_path)
        return self


class CasePlan(StrictModel):
    schema_version: Literal["1.0"]
    kind: Literal["sat", "shortest_path"]
    left: ToolSpec
    right: ToolSpec
    cases: list[CaseSpec] = Field(min_length=1, max_length=MAX_CASES)
    cutoff_seconds: int = Field(ge=1, le=MAX_CUTOFF)
    max_output_bytes: int = Field(ge=1, le=MAX_OUTPUT)
    seed: int = Field(ge=0, le=2_147_483_647)
    environment_note: str = Field(min_length=1, max_length=1000)
    design_note: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def check_unique(self) -> CasePlan:
        if self.left.name == self.right.name:
            raise ValueError("Paired tools must have distinct names")
        if len({c.case_id for c in self.cases}) != len(self.cases):
            raise ValueError("Duplicate case ID")
        return self


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("Duplicate JSON field")
        obj[key] = value
    return obj


def _constant(value: str) -> None:
    raise ValueError("Invalid JSON constant: " + value)


def _parse_json(body: bytes) -> Any:
    return json.loads(body.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)


def _validate_relative(raw: str) -> None:
    if (
        not _RELPATH.fullmatch(raw)
        or raw.startswith("/")
        or any(part in {"", ".", ".."} for part in raw.split("/"))
    ):
        raise CaseBundleError("Instance paths must be safe, relative, and normalized")


def _safe_file(root: Path, relative: str, *, maximum: int = MAX_PLAN) -> bytes:
    _validate_relative(relative)
    if root.is_symlink():
        raise CaseBundleError("Symlinked case-study root refused")
    target = root / relative
    if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
        raise CaseBundleError("Symlinked or escaped case-study file")
    if not target.is_file() or target.stat().st_size > maximum:
        raise CaseBundleError("Missing or oversized case-study file")
    return target.read_bytes()


def _write_new(root: Path, relative: str, data: bytes) -> None:
    _validate_relative(relative)
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as out:
        out.write(data)
        out.flush()
        os.fsync(out.fileno())


def read_plan(path: Path) -> CasePlan:
    if path.is_symlink() or path.stat().st_size > MAX_PLAN:
        raise CaseBundleError("Invalid case-study plan file")
    try:
        return CasePlan.model_validate(_parse_json(path.read_bytes()))
    except (ValueError, UnicodeError, ValidationError) as exc:
        raise CaseBundleError("Invalid versioned case-study plan") from exc


def _binary_sha(tool: ToolSpec) -> str:
    binary = Path(tool.argv[0])
    if not binary.is_file() or binary.stat().st_size > 100_000_000:
        raise CaseExecutionRefused("Missing or oversized trusted-local executable")
    result = digest_bytes(binary.read_bytes())
    if result != tool.binary_sha256:
        raise CaseExecutionRefused("Executable bytes do not match frozen SHA-256")
    for pinned_path, expected in tool.pinned_files.items():
        auxiliary = Path(pinned_path)
        if not auxiliary.is_file() or auxiliary.stat().st_size > MAX_PLAN:
            raise CaseExecutionRefused("Missing or oversized pinned auxiliary source")
        if digest_bytes(auxiliary.read_bytes()) != expected:
            raise CaseExecutionRefused("Pinned auxiliary source digest changed")
    return result


def run_cases(plan_file: Path, output: Path, *, approved: bool) -> dict[str, Any]:
    """Execute only explicitly trusted local binaries, creating a fresh raw-log bundle."""
    if not approved:
        raise CaseExecutionRefused("--approve-trusted-local is mandatory; no sandbox exists")
    plan = read_plan(plan_file)
    source_root = plan_file.parent
    cases: dict[str, bytes] = {}
    for item in plan.cases:
        data = _safe_file(source_root, item.input_path)
        if digest_bytes(data) != item.input_sha256:
            raise CaseBundleError("Plan input SHA-256 disagrees with original bytes")
        if plan.kind == "sat":
            parse_dimacs(data)
        else:
            from researchforge.case_oracles import read_graph

            read_graph(data)
        cases[item.case_id] = data
    for tool in (plan.left, plan.right):
        _binary_sha(tool)
    # Fail before any execution if the chosen output path already exists.
    output.mkdir(parents=True, exist_ok=False)
    _write_new(output, "plan.json", canonical_json(plan.model_dump(mode="json")))
    for item in plan.cases:
        suffix = "cnf" if plan.kind == "sat" else "json"
        _write_new(output, f"inputs/{item.case_id}.{suffix}", cases[item.case_id])
    rows: list[dict[str, Any]] = []
    for item in plan.cases:
        for tool in (plan.left, plan.right):
            _binary_sha(tool)  # Reject mid-campaign binary replacement.
            suffix = "cnf" if plan.kind == "sat" else "json"
            instance = (output / "inputs" / f"{item.case_id}.{suffix}").resolve()
            argv = [
                str(instance) if word == "{input}" else str(plan.seed) if word == "{seed}" else word
                for word in tool.argv
            ]
            code, stdout, stderr, failure, seconds = _bounded_process(
                argv,
                cwd=output.resolve(),
                timeout=plan.cutoff_seconds,
                max_output=plan.max_output_bytes,
                env={
                    k: v
                    for k, v in os.environ.items()
                    if k in {"PATH", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR"}
                },
            )
            if not math.isfinite(seconds) or seconds < 0:
                raise CaseBundleError("Invalid monotonic runtime measurement")
            base = f"logs/{item.case_id}/{tool.name}"
            _write_new(output, base + ".stdout", stdout)
            _write_new(output, base + ".stderr", stderr)
            verdict, reason = (
                oracle(plan.kind, cases[item.case_id], stdout, code)
                if failure is None
                else ("UNKNOWN", failure)
            )
            rows.append(
                {
                    "case_id": item.case_id,
                    "tool": tool.name,
                    "argv": argv,
                    "exit_code": code,
                    "failure": failure,
                    "runtime_seconds": seconds,
                    "stdout_path": base + ".stdout",
                    "stdout_sha256": digest_bytes(stdout),
                    "stderr_path": base + ".stderr",
                    "stderr_sha256": digest_bytes(stderr),
                    "correctness": verdict,
                    "reason": reason,
                }
            )
    body = {
        "schema_version": "1.0",
        "provenance": "EXECUTED_TRUSTED_LOCAL",
        "execution_root": str(output.resolve()),
        "plan_sha256": digest_bytes(_safe_file(output, "plan.json")),
        "rows": rows,
    }
    stored = canonical_json(body)
    _write_new(output, "bundle.json", stored)
    _write_new(output, "bundle.sha256", (digest_bytes(stored) + "\n").encode())
    return verify_bundle(output)


def _category(kind: str, data: bytes) -> str:
    if kind == "shortest_path":
        return "GRAPH"
    from researchforge.case_oracles import MAX_SAT_VARS_FOR_UNSAT, _has_solution

    nvars, clauses = parse_dimacs(data)
    if nvars > MAX_SAT_VARS_FOR_UNSAT:
        return "SAT_OR_UNSAT_UNCHECKED"
    return "SAT" if _has_solution(nvars, clauses) else "UNSAT"


def verify_bundle(folder: Path) -> dict[str, Any]:
    """Offline independent replay against included originals, not submitted statuses."""
    plan_bytes = _safe_file(folder, "plan.json")
    try:
        plan = CasePlan.model_validate(_parse_json(plan_bytes))
        body = _safe_file(folder, "bundle.json")
        supplied_digest = _safe_file(folder, "bundle.sha256").decode("ascii").strip()
        if digest_bytes(body) != supplied_digest or canonical_json(_parse_json(body)) != body:
            raise CaseBundleError("Noncanonical or tampered case-study bundle")
        result = _parse_json(body)
        if (
            not isinstance(result, dict)
            or set(result)
            != {"schema_version", "provenance", "execution_root", "plan_sha256", "rows"}
            or result["schema_version"] != "1.0"
            or result["provenance"] != "EXECUTED_TRUSTED_LOCAL"
            or not isinstance(result["execution_root"], str)
            or not Path(result["execution_root"]).is_absolute()
            or result["plan_sha256"] != digest_bytes(plan_bytes)
            or not isinstance(result["rows"], list)
            or len(result["rows"]) != len(plan.cases) * 2
        ):
            raise CaseBundleError("Invalid case-study receipt schema")
        by_pair: dict[tuple[str, str], dict[str, Any]] = {}
        for raw in result["rows"]:
            if not isinstance(raw, dict):
                raise CaseBundleError("Invalid case-study trial record")
            row_id, row_tool = raw.get("case_id"), raw.get("tool")
            if not isinstance(row_id, str) or not isinstance(row_tool, str):
                raise CaseBundleError("Invalid row case or tool identity")
            key = (row_id, row_tool)
            if key in by_pair:
                raise CaseBundleError("Repeated case/tool pair")
            if not isinstance(raw.get("runtime_seconds"), (float, int)) or (
                isinstance(raw["runtime_seconds"], bool)
                or not math.isfinite(raw["runtime_seconds"])
                or raw["runtime_seconds"] < 0
            ):
                raise CaseBundleError("Unusable recorded runtime")
            by_pair[key] = raw
        cases: list[dict[str, Any]] = []
        a_scores: dict[str, float] = {}
        b_scores: dict[str, float] = {}
        families: dict[str, dict[str, int]] = {}
        by_category: dict[str, dict[str, int]] = {}
        for item in plan.cases:
            suffix = "cnf" if plan.kind == "sat" else "json"
            data = _safe_file(folder, f"inputs/{item.case_id}.{suffix}")
            if digest_bytes(data) != item.input_sha256:
                raise CaseBundleError("Bundled instance conflicts with frozen plan")
            category = _category(plan.kind, data)
            summary: dict[str, Any] = {
                "case_id": item.case_id,
                "family": item.family,
                "category": category,
                "instance_sha256": item.input_sha256,
            }
            for label, tool in (("left", plan.left), ("right", plan.right)):
                row = by_pair.get((item.case_id, tool.name))
                if row is None:
                    raise CaseBundleError("Missing trial for case/tool pair")
                base = f"logs/{item.case_id}/{tool.name}"
                expected_input = (
                    Path(result["execution_root"]) / "inputs" / f"{item.case_id}.{suffix}"
                )
                expected_argv = [
                    str(expected_input)
                    if word == "{input}"
                    else str(plan.seed)
                    if word == "{seed}"
                    else word
                    for word in tool.argv
                ]
                if row.get("argv") != expected_argv:
                    raise CaseBundleError("Recorded argv contradicts frozen plan")
                if (
                    row.get("stdout_path") != base + ".stdout"
                    or row.get("stderr_path") != base + ".stderr"
                ):
                    raise CaseBundleError("Log paths are not tied to exact pair")
                stdout = _safe_file(folder, row["stdout_path"], maximum=MAX_OUTPUT)
                stderr = _safe_file(folder, row["stderr_path"], maximum=MAX_OUTPUT)
                if len(stdout) + len(stderr) > plan.max_output_bytes:
                    raise CaseBundleError("Logs exceed frozen per-trial output bound")
                if digest_bytes(stdout) != row.get("stdout_sha256") or digest_bytes(
                    stderr
                ) != row.get("stderr_sha256"):
                    raise CaseBundleError("Raw log digest mismatch")
                code = row.get("exit_code")
                if code is not None and (type(code) is not int or not -255 <= code <= 255):
                    raise CaseBundleError("Invalid process exit code")
                failure = row.get("failure")
                if failure is not None and failure not in {"TIMEOUT", "OUTPUT_LIMIT", "UNKNOWN"}:
                    raise CaseBundleError("Invalid process termination outcome")
                verified_status, reason = (
                    oracle(plan.kind, data, stdout, code)
                    if failure is None
                    else ("UNKNOWN", failure)
                )
                if (row.get("correctness"), row.get("reason")) != (verified_status, reason):
                    raise CaseBundleError("Recorded verdict contradicts independent oracle")
                success = verified_status in {"VERIFIED_SAT", "VERIFIED_UNSAT", "VERIFIED_GRAPH"}
                runtime = row["runtime_seconds"]
                par2 = runtime if success else 2.0 * plan.cutoff_seconds
                record = {
                    "tool": tool.name,
                    "status": verified_status,
                    "reason": reason,
                    "runtime_seconds": runtime,
                    "par2_seconds": par2,
                    "stdout_sha256": row["stdout_sha256"],
                    "stderr_sha256": row["stderr_sha256"],
                }
                summary[label] = record
                score = 1.0 if success else 0.0
                (a_scores if label == "left" else b_scores)[item.case_id] = score
                family_row = families.setdefault(
                    item.family, {plan.left.name: 0, plan.right.name: 0}
                )
                category_row = by_category.setdefault(
                    category, {plan.left.name: 0, plan.right.name: 0}
                )
                if success:
                    family_row[tool.name] += 1
                    category_row[tool.name] += 1
            cases.append(summary)
        left_only = sum(
            x["left"]["status"].startswith("VERIFIED")
            and not x["right"]["status"].startswith("VERIFIED")
            for x in cases
        )
        right_only = sum(
            x["right"]["status"].startswith("VERIFIED")
            and not x["left"]["status"].startswith("VERIFIED")
            for x in cases
        )
        comparison = paired_comparison(
            a_scores, b_scores, direction="max", seed=plan.seed, resamples=1000
        )
        return {
            "schema_version": "1.0",
            "kind": plan.kind,
            "source": "OFFLINE_REPLAY_OF_TRUSTED_LOCAL_RECEIPT",
            "integrity": "HASHES_AND_INDEPENDENT_SMALL_ORACLES",
            "bundle_sha256": supplied_digest,
            "plan_sha256": digest_bytes(plan_bytes),
            "tool_versions": {
                plan.left.name: {
                    "binary_sha256": plan.left.binary_sha256,
                    "version_or_commit": plan.left.version_or_commit,
                },
                plan.right.name: {
                    "binary_sha256": plan.right.binary_sha256,
                    "version_or_commit": plan.right.version_or_commit,
                },
            },
            "case_count": len(cases),
            "certified_solved": {
                plan.left.name: int(sum(a_scores.values())),
                plan.right.name: int(sum(b_scores.values())),
            },
            "paired_discordance": {"left_only": left_only, "right_only": right_only},
            "by_family": families,
            "by_category": by_category,
            "paired_solved_bootstrap": comparison,
            "par2_seconds": {
                plan.left.name: sum(x["left"]["par2_seconds"] for x in cases),
                plan.right.name: sum(x["right"]["par2_seconds"] for x in cases),
            },
            "cases": cases,
            "limitations": [
                "Runtime numbers are locally recorded wall times, not independently clock-verified",
                "Small-suite bootstrap is descriptive and cannot establish population superiority",
                "UNSAT above the exhaustive-check limit needs a separate independent proof checker",
                "Incorrect or unverified outputs receive no certified solved credit",
                "Local execution is no security sandbox; administrators can alter local evidence",
                "No external algorithm superiority claim without representative independent runs",
            ],
        }
    except (UnicodeError, OSError, ValueError, ValidationError) as exc:
        if isinstance(exc, CaseBundleError):
            raise
        raise CaseBundleError("Invalid/corrupt case-study bundle") from exc


case_commands = typer.Typer(help="Real trusted-local paired experiments and offline replay.")


@case_commands.command("run")
def case_run_cli(
    plan: Annotated[Path, typer.Option("--plan")],
    output: Annotated[Path, typer.Option("--output")],
    approve: Annotated[bool, typer.Option("--approve-trusted-local")] = False,
) -> None:
    """Run two explicitly trusted executables; this is NOT an OS sandbox."""
    try:
        typer.echo(json.dumps(run_cases(plan, output, approved=approve), sort_keys=True))
    except CaseExecutionRefused as exc:
        typer.echo(f"REFUSED: {exc}", err=True)
        raise typer.Exit(code=3) from exc
    except (CaseBundleError, CaseInputError, OSError, ValidationError) as exc:
        typer.echo(f"INVALID_OR_CORRUPT: {exc}", err=True)
        raise typer.Exit(code=6) from exc


@case_commands.command("verify")
def case_verify_cli(bundle: Annotated[Path, typer.Option("--bundle")]) -> None:
    """Only replay stored raw logs and original inputs, never execute or mutate."""
    try:
        typer.echo(json.dumps(verify_bundle(bundle), sort_keys=True))
    except (CaseBundleError, CaseInputError, OSError, ValidationError) as exc:
        typer.echo(f"CORRUPT: {exc}", err=True)
        raise typer.Exit(code=6) from exc

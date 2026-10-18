"""Small, deliberately independent correctness oracles for case-study outputs.

SAT models are checked clause-by-clause. UNSAT is established only with a
bounded exhaustive independent search, NOT by trusting the solver's claim.
Graph paths are checked against a separate Bellman-Ford implementation.
"""
from __future__ import annotations

import json
import re
from typing import Any, Literal

from researchforge.domain.serialization import canonical_json

MAX_INPUT_BYTES = 1_048_576
MAX_OUTPUT_BYTES = 1_048_576
MAX_SAT_VARS_FOR_UNSAT = 16
Status = Literal[
    "VERIFIED_SAT",
    "VERIFIED_UNSAT",
    "VERIFIED_GRAPH",
    "WRONG_ANSWER",
    "UNVERIFIED_UNSAT",
    "UNKNOWN",
]


class CaseInputError(ValueError):
    """Malformed instance, log, or case-study contract."""


def parse_dimacs(data: bytes) -> tuple[int, tuple[tuple[int, ...], ...]]:
    if len(data) > MAX_INPUT_BYTES:
        raise CaseInputError("DIMACS instance too large")
    try:
        content = data.decode("ascii")
    except UnicodeError as exc:
        raise CaseInputError("DIMACS must be ASCII") from exc
    nvars: int | None = None
    declared: int | None = None
    clauses: list[tuple[int, ...]] = []
    pending: list[int] = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("c"):
            continue
        if line.startswith("p "):
            parts = line.split()
            if nvars is not None or len(parts) != 4 or parts[1] != "cnf":
                raise CaseInputError("Invalid or duplicate DIMACS header")
            try:
                nvars, declared = int(parts[2]), int(parts[3])
            except ValueError as exc:
                raise CaseInputError("Invalid DIMACS dimensions") from exc
            if nvars < 0 or nvars > 100_000 or declared < 0 or declared > 500_000:
                raise CaseInputError("DIMACS dimensions out of bounds")
            continue
        if nvars is None:
            raise CaseInputError("Clause precedes DIMACS header")
        for token in line.split():
            try:
                literal = int(token)
            except ValueError as exc:
                raise CaseInputError("Noninteger DIMACS literal") from exc
            if literal == 0:
                clauses.append(tuple(pending))
                pending = []
            elif abs(literal) > nvars:
                raise CaseInputError("DIMACS literal outside declared variable range")
            else:
                pending.append(literal)
    if nvars is None or declared is None or pending or len(clauses) != declared:
        raise CaseInputError("Missing header, truncated clause or clause count mismatch")
    return nvars, tuple(clauses)


def _satisfies(clauses: tuple[tuple[int, ...], ...], assignment: dict[int, bool]) -> bool:
    return all(
        any(assignment.get(abs(literal)) == (literal > 0) for literal in clause)
        for clause in clauses
    )


def _has_solution(nvars: int, clauses: tuple[tuple[int, ...], ...]) -> bool:
    for bits in range(1 << nvars):
        assignment = {v: bool(bits & (1 << (v - 1))) for v in range(1, nvars + 1)}
        if _satisfies(clauses, assignment):
            return True
    return False


def verify_sat(data: bytes, stdout: bytes, exit_code: int | None) -> tuple[Status, str]:
    nvars, clauses = parse_dimacs(data)
    if len(stdout) > MAX_OUTPUT_BYTES:
        return "UNKNOWN", "TRUNCATED_OUTPUT"
    try:
        decoded = stdout.decode("ascii")
    except UnicodeError:
        return "UNKNOWN", "NON_ASCII_SOLVER_OUTPUT"
    declarations: list[str] = []
    literals: list[int] = []
    try:
        for raw in decoded.splitlines():
            line = raw.strip()
            if line.startswith("s "):
                declarations.append(line[2:].strip())
            elif line.startswith("v "):
                literals.extend(int(v) for v in line[2:].split())
        if len(declarations) != 1:
            return "UNKNOWN", "MISSING_OR_AMBIGUOUS_STATUS"
        status = declarations[0]
        if status == "UNKNOWN":
            return "UNKNOWN", "SOLVER_RETURNED_UNKNOWN"
        if status not in {"SATISFIABLE", "UNSATISFIABLE"}:
            return "UNKNOWN", "UNRECOGNIZED_SOLVER_STATUS"
        if exit_code not in ({0, 10} if status == "SATISFIABLE" else {0, 20}):
            return "UNKNOWN", "SOLVER_EXIT_CODE_MISMATCH"
        if status == "UNSATISFIABLE":
            if literals:
                return "WRONG_ANSWER", "UNSAT_OUTPUT_WITH_MODEL"
            if nvars > MAX_SAT_VARS_FOR_UNSAT:
                return "UNVERIFIED_UNSAT", "INDEPENDENT_PROOF_NOT_AVAILABLE"
            if _has_solution(nvars, clauses):
                return "WRONG_ANSWER", "FALSE_UNSAT_CLAIM"
            return "VERIFIED_UNSAT", "EXHAUSTIVE_INDEPENDENT_CHECK"
        model: dict[int, bool] = {}
        for literal in literals:
            if literal == 0:
                continue
            if abs(literal) > nvars or literal == 0:
                return "WRONG_ANSWER", "MODEL_OUT_OF_RANGE"
            var = abs(literal)
            value = literal > 0
            if var in model and model[var] != value:
                return "WRONG_ANSWER", "CONFLICTING_MODEL_LITERALS"
            model[var] = value
        if not _satisfies(clauses, model):
            return "WRONG_ANSWER", "MODEL_DOES_NOT_SATISFY_CNF"
        return "VERIFIED_SAT", "CLAUSE_BY_CLAUSE_WITNESS_CHECK"
    except ValueError:
        return "UNKNOWN", "MALFORMED_SOLVER_MODEL"


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for name, value in pairs:
        if name in obj:
            raise ValueError("Duplicate JSON key")
        obj[name] = value
    return obj


def _no_constants(value: str) -> None:
    raise ValueError(f"Nonfinite JSON literal: {value}")


def read_graph(data: bytes) -> tuple[tuple[str, ...], str, list[tuple[str, str, int]]]:
    if len(data) > MAX_INPUT_BYTES:
        raise CaseInputError("Graph instance too large")
    try:
        obj = json.loads(
            data.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_no_constants
        )
        if not isinstance(obj, dict) or set(obj) != {"nodes", "source", "edges"}:
            raise ValueError("Graph schema must have nodes/source/edges")
        nodes = obj["nodes"]
        source = obj["source"]
        edges = obj["edges"]
        if (
            not isinstance(nodes, list)
            or len(nodes) == 0
            or len(nodes) > 256
            or not all(
                isinstance(n, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,64}", n)
                for n in nodes
            )
            or len(nodes) != len(set(nodes))
            or not isinstance(source, str)
            or source not in nodes
            or not isinstance(edges, list)
            or len(edges) > 4096
        ):
            raise ValueError("Invalid graph node collection")
        converted: list[tuple[str, str, int]] = []
        for edge in edges:
            if (
                not isinstance(edge, list)
                or len(edge) != 3
                or not all(isinstance(v, str) and v in nodes for v in edge[:2])
                or type(edge[2]) is not int
                or not 0 <= edge[2] <= 1_000_000
            ):
                raise ValueError("Invalid nonnegative directed graph edge")
            converted.append((edge[0], edge[1], edge[2]))
        return tuple(nodes), source, converted
    except (UnicodeError, ValueError, TypeError) as exc:
        raise CaseInputError("Invalid versionless graph instance") from exc


def _bellman_ford(
    nodes: tuple[str, ...], source: str, edges: list[tuple[str, str, int]]
) -> dict[str, int | None]:
    distance: dict[str, int | None] = dict.fromkeys(nodes)
    distance[source] = 0
    for _ in range(len(nodes) - 1):
        changed = False
        for u, v, weight in edges:
            du = distance[u]
            dv = distance[v]
            if du is not None and (dv is None or du + weight < dv):
                distance[v] = du + weight
                changed = True
        if not changed:
            break
    return distance


def verify_graph(data: bytes, stdout: bytes, exit_code: int | None) -> tuple[Status, str]:
    nodes, source, edges = read_graph(data)
    if exit_code != 0 or len(stdout) > MAX_OUTPUT_BYTES:
        return "UNKNOWN", "GRAPH_NONZERO_EXIT_OR_TRUNCATED"
    try:
        obj = json.loads(
            stdout.decode("utf-8"), object_pairs_hook=_unique, parse_constant=_no_constants
        )
        if not isinstance(obj, dict) or set(obj) != {"distances"}:
            raise ValueError("Expected only distances")
        distances = obj["distances"]
        if (
            not isinstance(distances, dict)
            or set(distances) != set(nodes)
            or any(v is not None and type(v) is not int for v in distances.values())
        ):
            raise ValueError("Invalid graph distance map")
        expected = _bellman_ford(nodes, source, edges)
        if distances != expected:
            return "WRONG_ANSWER", "BELLMAN_FORD_ORACLE_MISMATCH"
        return "VERIFIED_GRAPH", "INDEPENDENT_BELLMAN_FORD_ORACLE"
    except (UnicodeError, ValueError, TypeError):
        return "UNKNOWN", "MALFORMED_GRAPH_RESULT"


def oracle(kind: Literal["sat", "shortest_path"], data: bytes, stdout: bytes,
           exit_code: int | None) -> tuple[Status, str]:
    if kind == "sat":
        return verify_sat(data, stdout, exit_code)
    return verify_graph(data, stdout, exit_code)


def normalized_json(value: Any) -> bytes:
    """Canonical JSON encoder used by test fixtures and stored receipts."""
    return canonical_json(value)

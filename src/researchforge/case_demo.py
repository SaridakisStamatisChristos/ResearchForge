"""Executed, fully offline *toy* SAT and shortest-path case-study smoke run.

This exercises real Python subprocesses with actual input/output bytes. It is NOT
a SatSolv/Kissat comparison or an ADES external benchmark.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from researchforge.case_studies import run_cases, verify_bundle
from researchforge.domain.serialization import canonical_json, digest_bytes

SAT_PROGRAM = r"""
import sys
from itertools import product
from pathlib import Path

path, mode = sys.argv[1:3]
lines = Path(path).read_text(encoding="ascii").splitlines()
header = next(line for line in lines if line.startswith("p "))
nvars = int(header.split()[2])
tokens = [int(w) for line in lines if line and line[0] not in {"c", "p"}
          for w in line.split()]
clauses, clause = [], []
for literal in tokens:
    if literal == 0:
        clauses.append(clause)
        clause = []
    else:
        clause.append(literal)
solution = None
for vector in product((False, True), repeat=nvars):
    values = {i + 1: value for i, value in enumerate(vector)}
    if all(any(values[abs(v)] == (v > 0) for v in c) for c in clauses):
        solution = values
        break
if solution is None:
    print("s UNSATISFIABLE")
    sys.exit(20)
print("s SATISFIABLE")
if mode == "intentionally-wrong" and "trap" in path:
    print("v " + " ".join(str(-i) for i in range(1, nvars + 1)) + " 0")
else:
    print("v " + " ".join(str(i if solution[i] else -i)
                         for i in range(1, nvars + 1)) + " 0")
sys.exit(10)
"""

GRAPH_PROGRAM = r"""
import json
import sys
from pathlib import Path

path, mode = sys.argv[1:3]
data = json.loads(Path(path).read_text())
nodes = data["nodes"]
source = data["source"]
edges = data["edges"]
dist = {node: None for node in nodes}
dist[source] = 0
unvisited = set(nodes)
while unvisited:
    reachable = [x for x in unvisited if dist[x] is not None]
    if not reachable:
        break
    node = min(reachable, key=lambda x: dist[x])
    unvisited.remove(node)
    for u, v, weight in edges:
        if u == node:
            alternative = dist[node] + weight
            if dist[v] is None or alternative < dist[v]:
                dist[v] = alternative
if mode == "intentionally-wrong" and "trap" in path and dist.get("t") is not None:
    dist["t"] += 1
print(json.dumps({"distances": dist}, sort_keys=True))
"""


def _write(folder: Path, path: str, body: bytes) -> None:
    target = folder / path
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as stream:
        stream.write(body)


def _case_input(kind: str, case_id: str) -> bytes:
    if kind == "sat":
        content = {
            "sat-one": b"c satisfiable\np cnf 1 1\n1 0\n",
            "unsat-one": b"p cnf 1 2\n1 0\n-1 0\n",
            "sat-trap": b"p cnf 2 2\n1 2 0\n-1 2 0\n",
        }
        return content[case_id]
    graph_examples: dict[str, dict[str, Any]] = {
        "path-one": {
            "nodes": ["s", "a", "t"],
            "source": "s",
            "edges": [["s", "a", 2], ["a", "t", 3], ["s", "t", 9]],
        },
        "disconnected": {
            "nodes": ["s", "t"],
            "source": "s",
            "edges": [],
        },
        "path-trap": {
            "nodes": ["s", "a", "t"],
            "source": "s",
            "edges": [["s", "a", 0], ["a", "t", 1], ["s", "t", 7]],
        },
    }
    return canonical_json(graph_examples[case_id])


def run_case_demo(root: Path) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=False)
    result: dict[str, Any] = {"kind": "OFFLINE_TOY_REFERENCE_FIXTURES", "domains": {}}
    binary = Path(sys.executable).resolve()
    binary_sha = digest_bytes(binary.read_bytes())
    for kind, ids, script in (
        ("sat", ["sat-one", "unsat-one", "sat-trap"], SAT_PROGRAM),
        ("shortest_path", ["path-one", "disconnected", "path-trap"], GRAPH_PROGRAM),
    ):
        source = root / kind
        source.mkdir()
        program = source / "toy_program.py"
        _write(source, "toy_program.py", script.encode("utf-8"))
        cases = []
        for case_id in ids:
            body = _case_input(kind, case_id)
            suffix = "cnf" if kind == "sat" else "json"
            relpath = f"cases/{case_id}.{suffix}"
            _write(source, relpath, body)
            cases.append(
                {
                    "case_id": case_id,
                    "family": "toy-sat" if kind == "sat" else "toy-graph",
                    "input_path": relpath,
                    "input_sha256": digest_bytes(body),
                }
            )
        plan = {
            "schema_version": "1.0",
            "kind": kind,
            "left": {
                "name": "toy-reference",
                "argv": [str(binary), str(program), "{input}", "reference"],
                "binary_sha256": binary_sha,
                "pinned_files": {str(program): digest_bytes(program.read_bytes())},
                "version_or_commit": (
                    f"toy-python-{sys.version_info.major}.{sys.version_info.minor}"
                ),
            },
            "right": {
                "name": "toy-defective",
                "argv": [str(binary), str(program), "{input}", "intentionally-wrong"],
                "binary_sha256": binary_sha,
                "pinned_files": {str(program): digest_bytes(program.read_bytes())},
                "version_or_commit": "same-source-intentionally-faulty-mode",
            },
            "cases": cases,
            "cutoff_seconds": 5,
            "max_output_bytes": 65536,
            "seed": 42,
            "environment_note": "Only this explicitly trusted offline Python toy fixture",
            "design_note": "Smoke/recovery evidence; NOT any third-party solver superiority claim",
        }
        plan_path = source / "plan.json"
        _write(source, "plan.json", canonical_json(plan))
        bundle_path = source / "raw-bundle"
        executed = run_cases(plan_path, bundle_path, approved=True)
        replayed = verify_bundle(bundle_path)
        if executed != replayed:
            raise ValueError("Offline independent replay diverged")
        if (
            executed["certified_solved"]["toy-reference"] != len(ids)
            or executed["certified_solved"]["toy-defective"] != len(ids) - 1
        ):
            raise ValueError("Expected negative evidence was not preserved")
        _write(source, "verified-report.json", canonical_json(replayed) + b"\n")
        result["domains"][kind] = {
            "case_count": len(ids),
            "verified_reference": executed["certified_solved"]["toy-reference"],
            "verified_defective": executed["certified_solved"]["toy-defective"],
            "raw_bundle_sha256": executed["bundle_sha256"],
            "summary": str(source / "verified-report.json"),
        }
    _write(root, "summary.json", canonical_json(result) + b"\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run real offline toy case-study processes")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run_case_demo(args.output), sort_keys=True))


if __name__ == "__main__":
    main()

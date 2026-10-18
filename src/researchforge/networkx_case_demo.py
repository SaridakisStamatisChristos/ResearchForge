"""PR13 independent open-source NetworkX case study with real local processes.

Runs the unchanged NetworkX 3.6.1 algorithms through thin, digest-pinned
Python adapters; this is a compatibility/correctness demonstration and not
a comparative performance superiority claim.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

from researchforge.case_studies import run_cases, verify_bundle
from researchforge.domain.serialization import canonical_json, digest_bytes
from researchforge.networkx_solver import NETWORKX_VERSION


def graph_cases() -> list[tuple[str, str, dict[str, Any]]]:
    """Frozen, deterministic, small, intentionally varied directed graph corpus."""
    cases: list[tuple[str, str, dict[str, Any]]] = [
        (
            "chain",
            "path",
            {
                "nodes": ["s", "a", "b", "t"],
                "source": "s",
                "edges": [["s", "a", 2], ["a", "b", 3], ["b", "t", 7]],
            },
        ),
        (
            "disconnected",
            "disconnected",
            {
                "nodes": ["s", "a", "t", "isolated"],
                "source": "s",
                "edges": [["s", "a", 2]],
            },
        ),
        (
            "zero-weights",
            "zero-weight",
            {
                "nodes": ["s", "a", "t"],
                "source": "s",
                "edges": [["s", "a", 0], ["a", "t", 0], ["s", "t", 5]],
            },
        ),
        (
            "parallel",
            "multi-edge",
            {
                "nodes": ["s", "t", "x"],
                "source": "s",
                "edges": [["s", "t", 9], ["s", "t", 2], ["s", "x", 1], ["x", "t", 3]],
            },
        ),
        (
            "cycle",
            "cyclic",
            {
                "nodes": ["s", "a", "b", "t"],
                "source": "s",
                "edges": [
                    ["s", "a", 1],
                    ["a", "b", 1],
                    ["b", "a", 0],
                    ["b", "t", 1],
                    ["t", "s", 13],
                ],
            },
        ),
        (
            "isolated-source",
            "disconnected",
            {"nodes": ["s", "x", "t"], "source": "s", "edges": [["x", "t", 2]]},
        ),
    ]
    rng = random.Random(13013)
    for size in (8, 12, 16, 20, 24, 30):
        names = [f"n{index}" for index in range(size)]
        edges: list[list[str | int]] = []
        for index in range(size - 1):
            edges.append([names[index], names[index + 1], rng.randrange(0, 50)])
        for _ in range(size * 2):
            a, b = rng.randrange(size), rng.randrange(size)
            if a != b:
                edges.append([names[a], names[b], rng.randrange(0, 100)])
        cases.append(
            (
                f"seeded-{size}",
                "seeded-sparse",
                {"nodes": names, "source": names[0], "edges": edges},
            )
        )
    return cases


def run_networkx_case_study(root: Path) -> dict[str, Any]:
    """Execute a fresh two-arm, 12-case third-party algorithms study."""
    import networkx as nx  # type: ignore[import-untyped]  # NetworkX lacks bundled stubs

    if nx.__version__ != NETWORKX_VERSION:
        raise RuntimeError("NetworkX version does not match pinned experimental dependency")

    from researchforge import networkx_solver

    python = Path(sys.executable).resolve()
    adapter = Path(networkx_solver.__file__).resolve()
    binary_sha256 = digest_bytes(python.read_bytes())
    adapter_sha256 = digest_bytes(adapter.read_bytes())

    root.mkdir(parents=True, exist_ok=False)
    cases: list[dict[str, str]] = []
    for case_id, family, content in graph_cases():
        payload = canonical_json(content)
        relative = f"inputs/{case_id}.json"
        path = root / relative
        path.parent.mkdir(exist_ok=True)
        with path.open("xb") as handle:
            handle.write(payload)
        cases.append(
            {
                "case_id": case_id,
                "family": family,
                "input_path": relative,
                "input_sha256": digest_bytes(payload),
            }
        )
    tools: dict[str, dict[str, Any]] = {}
    for label, method in (
        ("networkx-dijkstra", "dijkstra"),
        ("networkx-bellman-ford", "bellman-ford"),
    ):
        tools[label] = {
            "name": label,
            "argv": [str(python), str(adapter), "{input}", method],
            "binary_sha256": binary_sha256,
            "pinned_files": {str(adapter): adapter_sha256},
            "version_or_commit": f"networkx=={NETWORKX_VERSION}/{method}",
        }
    plan = {
        "schema_version": "1.0",
        "kind": "shortest_path",
        "left": tools["networkx-dijkstra"],
        "right": tools["networkx-bellman-ford"],
        "cases": cases,
        "cutoff_seconds": 10,
        "max_output_bytes": 65536,
        "seed": 13013,
        "environment_note": (
            "CPython local subprocess with NetworkX 3.6.1 from PyPI; "
            "startup time included in elapsed wall time"
        ),
        "design_note": (
            "External third-party algorithm correctness/integration case study; "
            "not evidence of broad performance superiority"
        ),
    }
    plan_file = root / "plan.json"
    with plan_file.open("xb") as handle:
        handle.write(canonical_json(plan))
    bundle = root / "raw-bundle"
    executed = run_cases(plan_file, bundle, approved=True)
    checked = verify_bundle(bundle)
    if executed != checked:
        raise RuntimeError("NetworkX raw-output replay does not match executed case study")
    expected = len(cases)
    if checked["certified_solved"] != {
        "networkx-dijkstra": expected,
        "networkx-bellman-ford": expected,
    }:
        raise RuntimeError("Third-party output violates independent Bellman-Ford oracle")
    if checked["paired_discordance"] != {"left_only": 0, "right_only": 0}:
        raise RuntimeError("Unexpected paired correctness discrepancy")
    report = root / "verified-report.json"
    with report.open("xb") as handle:
        handle.write(canonical_json(checked) + b"\n")
    summary = {
        "source": "THIRD_PARTY_NETWORKX_3_6_1_REAL_SUBPROCESSES",
        "upstream": "https://github.com/networkx/networkx",
        "package": f"networkx=={NETWORKX_VERSION}",
        "algorithm_pair": list(tools),
        "case_count": expected,
        "certified_solved": checked["certified_solved"],
        "bundle_sha256": checked["bundle_sha256"],
        "pinned_adapter_sha256": adapter_sha256,
        "report_file": str(report),
        "limitations": [
            "These are small deterministic graphs, not a representative performance study",
            "Elapsed wall time includes Python startup, import and process scheduling",
            "NetworkX dependency version is pinned but installed package bytes are not attested",
            "Both algorithms come from one upstream project; verification is independent code",
        ],
    }
    with (root / "summary.json").open("xb") as handle:
        handle.write(canonical_json(summary) + b"\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Third-party NetworkX PR13 case study")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(run_networkx_case_study(arguments.output), sort_keys=True))


if __name__ == "__main__":
    main()

"""Trusted-local NetworkX 3.6.1 executable adapter for PR13 case-study plans.

Executed only with explicit case-study approval; validates the input with the
separate ResearchForge graph schema and emits the normalized distance contract.
NetworkX is a third-party BSD-3-Clause project, not bundled with ResearchForge.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from researchforge.case_oracles import read_graph

NETWORKX_VERSION = "3.6.1"


def solve(input_path: Path, algorithm: str) -> dict[str, int | None]:
    import networkx as nx

    if nx.__version__ != NETWORKX_VERSION:
        raise RuntimeError("NetworkX dependency version does not match pinned case-study version")
    nodes, source, edges = read_graph(input_path.read_bytes())
    graph = nx.MultiDiGraph()
    graph.add_nodes_from(nodes)
    for tail, head, weight in edges:
        graph.add_edge(tail, head, weight=weight)
    if algorithm == "dijkstra":
        measured = nx.single_source_dijkstra_path_length(graph, source, weight="weight")
    elif algorithm == "bellman-ford":
        measured = nx.single_source_bellman_ford_path_length(graph, source, weight="weight")
    else:
        raise ValueError("Choose dijkstra or bellman-ford")
    return {node: int(measured[node]) if node in measured else None for node in nodes}


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("Usage: networkx_solver.py GRAPH.json dijkstra|bellman-ford")
    results = solve(Path(sys.argv[1]), sys.argv[2])
    print(json.dumps({"distances": results}, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()

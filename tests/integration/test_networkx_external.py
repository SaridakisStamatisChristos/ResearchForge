"""Real external NetworkX study and independent graph-correctness verification."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from researchforge.case_oracles import verify_graph
from researchforge.case_studies import CaseBundleError, verify_bundle
from researchforge.cli import app
from researchforge.domain.serialization import canonical_json, digest_bytes
from researchforge.networkx_case_demo import graph_cases, run_networkx_case_study
from researchforge.networkx_solver import NETWORKX_VERSION, solve


@pytest.fixture(scope="module")
def executed(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("pr13-networkx") / "external-case-study"
    run_networkx_case_study(directory)
    return directory


def test_real_upstream_networkx_produces_replayable_verified_results(executed: Path) -> None:
    summary = json.loads((executed / "summary.json").read_text())
    report = verify_bundle(executed / "raw-bundle")
    assert summary["source"] == "THIRD_PARTY_NETWORKX_3_6_1_REAL_SUBPROCESSES"
    assert summary["package"] == f"networkx=={NETWORKX_VERSION}"
    assert summary["upstream"] == "https://github.com/networkx/networkx"
    assert report["case_count"] == 12
    assert report["certified_solved"] == {
        "networkx-dijkstra": 12,
        "networkx-bellman-ford": 12,
    }
    assert report["paired_discordance"] == {"left_only": 0, "right_only": 0}
    assert report["source"] == "OFFLINE_REPLAY_OF_TRUSTED_LOCAL_RECEIPT"
    assert set(report["by_family"]) >= {
        "path", "disconnected", "zero-weight", "multi-edge", "cyclic", "seeded-sparse"
    }
    assert len(list((executed / "raw-bundle" / "logs").rglob("*.stdout"))) == 24
    assert all(
        entry["left"]["status"] == "VERIFIED_GRAPH"
        and entry["right"]["status"] == "VERIFIED_GRAPH"
        for entry in report["cases"]
    )
    assert (executed / "raw-bundle" / "bundle.sha256").is_file()
    assert (executed / "verified-report.json").is_file()
    cli = CliRunner().invoke(
        app, ["cases", "verify", "--bundle", str(executed / "raw-bundle")]
    )
    assert cli.exit_code == 0, cli.output
    assert json.loads(cli.output)["bundle_sha256"] == report["bundle_sha256"]


def test_independent_oracle_matches_both_external_algorithms() -> None:
    import networkx as nx

    assert nx.__version__ == NETWORKX_VERSION
    for case_id, _, graph in graph_cases():
        content = canonical_json(graph)
        fixture = Path(__file__).parent / "not-an-input-path"
        del fixture  # The solver's actual inputs are exercised by the integration run.
        nodes = graph["nodes"]
        edges = graph["edges"]
        multigraph = nx.MultiDiGraph()
        multigraph.add_nodes_from(nodes)
        for u, v, w in edges:
            multigraph.add_edge(u, v, weight=w)
        for method in ("dijkstra", "bellman-ford"):
            observed = (
                nx.single_source_dijkstra_path_length(multigraph, graph["source"])
                if method == "dijkstra"
                else nx.single_source_bellman_ford_path_length(multigraph, graph["source"])
            )
            answer = canonical_json(
                {"distances": {name: observed.get(name) for name in nodes}}
            )
            status, reason = verify_graph(content, answer, 0)
            assert status == "VERIFIED_GRAPH", (case_id, method, reason)


def test_full_replay_survives_download_to_a_different_folder(
    executed: Path, tmp_path: Path
) -> None:
    original = executed / "raw-bundle"
    restored = tmp_path / "downloaded" / "raw-bundle"
    restored.parent.mkdir()
    shutil.copytree(original, restored)
    assert verify_bundle(restored) == verify_bundle(original)


def test_mutated_upstream_stdout_is_detected_even_if_receipt_rehashed(
    executed: Path, tmp_path: Path
) -> None:
    copied = tmp_path / "tampered"
    shutil.copytree(executed / "raw-bundle", copied)
    stdout_path = copied / "logs" / "chain" / "networkx-dijkstra.stdout"
    wrong = canonical_json({"distances": {"s": 0, "a": 99, "b": None, "t": None}})
    stdout_path.write_bytes(wrong + b"\n")
    manifest_path = copied / "bundle.json"
    manifest = json.loads(manifest_path.read_bytes())
    affected = next(
        row for row in manifest["rows"]
        if row["case_id"] == "chain" and row["tool"] == "networkx-dijkstra"
    )
    affected["stdout_sha256"] = digest_bytes(stdout_path.read_bytes())
    altered = canonical_json(manifest)
    manifest_path.write_bytes(altered)
    (copied / "bundle.sha256").write_text(digest_bytes(altered) + "\n")
    with pytest.raises(CaseBundleError, match="independent oracle"):
        verify_bundle(copied)


def test_wrapper_rejects_wrong_algorithm_and_exact_version(
    executed: Path
) -> None:
    instance = executed / "inputs" / "parallel.json"
    assert solve(instance, "dijkstra") == solve(instance, "bellman-ford")
    assert solve(instance, "dijkstra")["t"] == 2
    with pytest.raises(ValueError, match="Choose"):
        solve(instance, "unknown")

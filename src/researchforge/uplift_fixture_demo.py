"""Execute an explicitly mechanical PR14 control/full/ablation fixture.

This is a subprocess wiring and replay test, NOT a real language-model uplift study.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from researchforge.domain.serialization import canonical_json, digest_bytes
from researchforge.uplift import run_uplift


def fixture_plan(root: Path) -> Path:
    """Create a frozen plan with absolute executable/source SHA pins."""
    skills = root / "skills"
    skills.mkdir()
    sources = {
        "parity_rule": b"Use parity of the integer to classify EVEN vs ODD.\n",
        "sign_rule": b"Use sign of the integer to classify POSITIVE vs NEGATIVE.\n",
    }
    for name, value in sources.items():
        (skills / (name + ".md")).write_bytes(value)
    script = Path(__file__).parents[2] / "examples" / "agent-uplift" / "fixture_agent.py"
    script = script.resolve()
    executable = Path(sys.executable).resolve()
    commands = [
        str(executable),
        str(script),
        "--task",
        "{task}",
        "--output",
        "{output}",
        "--context",
        "{context}",
    ]
    tasks = []
    for number in (1, 2, 3, 4):
        tasks.append(
            {
                "task_id": f"parity{number}",
                "family": "parity",
                "prompt": f"PARITY {number}",
                "expected_answer": "EVEN" if number % 2 == 0 else "ODD",
            }
        )
    for number in (-4, 5, -6, 7):
        tasks.append(
            {
                "task_id": f"sign{'n' if number < 0 else 'p'}{abs(number)}",
                "family": "sign",
                "prompt": f"SIGN {number}",
                "expected_answer": "NEGATIVE" if number < 0 else "POSITIVE",
            }
        )

    def arm(name: str, condition: str, components: list[str]) -> dict[str, Any]:
        return {
            "name": name,
            "condition": condition,
            "components": components,
            "argv": commands,
            "executable_sha256": digest_bytes(executable.read_bytes()),
            "pinned_files": {str(script): digest_bytes(script.read_bytes())},
            "agent_identity": "deterministic-python-fixture-v1",
        }

    plan = {
        "schema_version": "1.0",
        "study_id": "offline-mechanical-validation",
        "provenance_class": "MECHANICAL_FIXTURE",
        "design_note": "Test subprocess scheduling, ablations, evidence and scoring.",
        "task_sampling": "Fixed engineering-test cases, neither representative nor random.",
        "seed": 20261018,
        "bootstrap_resamples": 500,
        "timeout_seconds": 10,
        "max_output_bytes": 65536,
        "max_total_wall_seconds": 300,
        "allowed_environment_keys": [],
        "tasks": tasks,
        "skills": {
            k: {"path": f"skills/{k}.md", "sha256": digest_bytes(v)}
            for k, v in sources.items()
        },
        "arms": [
            arm("control", "control", []),
            arm("full", "full", list(sources)),
            arm("without_parity", "ablation", ["sign_rule"]),
            arm("without_sign", "ablation", ["parity_rule"]),
        ],
    }
    target = root / "suite.json"
    target.write_bytes(canonical_json(plan))
    return target


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="researchforge-pr14-") as workspace:
        report = run_uplift(fixture_plan(Path(workspace)), args.output, approved=True)
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()

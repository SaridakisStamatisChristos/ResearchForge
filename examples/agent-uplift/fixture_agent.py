"""Deterministic fixture, not an LLM or evidence of actual model uplift."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--context", type=Path, required=True)
    args = parser.parse_args()
    task = json.loads(args.task.read_text(encoding="utf-8"))
    kind, number_text = task["prompt"].split(" ", 1)
    number = int(number_text)
    if not re.fullmatch(r"-?[0-9]+", number_text):
        raise ValueError("Invalid toy number")
    if kind == "PARITY":
        enabled = (args.context / "parity_rule.md").is_file()
        answer = "EVEN" if not enabled or number % 2 == 0 else "ODD"
    elif kind == "SIGN":
        enabled = (args.context / "sign_rule.md").is_file()
        answer = "NEGATIVE" if enabled and number < 0 else "POSITIVE"
    else:
        raise ValueError("Unknown toy task")
    args.output.write_text(
        json.dumps({"answer": answer}, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

"""Generate or check committed v1 JSON schemas from the public Python contracts."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from researchforge.domain.models import (  # noqa: E402
    CampaignManifest,
    EvaluationResult,
    EvidenceEvent,
    FrozenManifest,
)

MODELS = {
    "campaign-v1.schema.json": CampaignManifest,
    "manifest-lock-v1.schema.json": FrozenManifest,
    "evaluator-result-v1.schema.json": EvaluationResult,
    "evidence-event-v1.schema.json": EvidenceEvent,
}


def main() -> int:
    check = "--check" in sys.argv[1:]
    bad = []
    for name, model in MODELS.items():
        content = json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"
        path = ROOT / "schemas" / name
        if check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                bad.append(name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    if bad:
        print("Schema drift: " + ", ".join(bad), file=sys.stderr)
        return 1
    print("Schema contracts verified" if check else "Schema contracts generated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

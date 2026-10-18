---
name: researchforge-audit
description: Mechanically validate hash-linked research assertions against replayed campaign and literature evidence with clear unknowns and contradictions.
compatibility: Python 3.12+ and ResearchForge CLI; offline, without evaluator execution.
metadata:
  version: "0.1.0"
---

# Claim audit skill

Use the actual ResearchAuditor CLI to check typed predicates, not LLM
judgment about the free-text claim. Supply a versioned claims JSON with exact
campaign event SHA-256 or literature snapshot SHA-256 identities.

~~~bash
rf audit claims --input claims.json --campaign /tmp/rf-demo/campaign --format json
rf audit claims --input claims.json --literature-store /tmp/rf-lit --format md
~~~

Example claim shape:

~~~json
{"schema_version":"1.0","claims":[{"claim_id":"final-gate","text":"Frozen final gate met","scope":"Frozen campaign only","kind":"final_status","expected":"FINAL_SUPPORTED","evidence":[{"source":"campaign_event","sha256":"REPLACE_WITH_ACTUAL_64_HEX_HASH"}]}]}
~~~

Replace the placeholder with the recorded final event identity.
VERIFIED only means the typed final predicate matches independently replayed
machine evidence. OBSERVED literature and qualification findings are not
independent scientific proof. Keep CONTRADICTED, INCONCLUSIVE, UNVERIFIED and
NOT_EVALUATED distinct. Do not treat query exit code zero as scientific
validation, and never claim semantic verification of arbitrary prose.
No network, code execution or Git merge is authorized by this skill.

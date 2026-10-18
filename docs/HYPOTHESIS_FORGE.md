# HypothesisForge (RF-ARCH-001 roadmap PR11)

HypothesisForge turns a **proposal** into a falsifiable, reviewed research
design; it does not invent experiments or verify their scientific claims.
Existing `rf hypothesis add` and candidate/qualification behavior remain
backwards-compatible. No model or API key is required for human-only use.

## Workflow

Create `design.json` containing the `hypothesis-design` schema, or copy
`examples/hypothesisforge/design.json`. The required fields capture the
proposition, intended intervention, causal mechanism, explicit assumptions,
confounders, metric and direction, falsifier, bounded test plan, and attempt
budget. Inspect the JSON schema with:

```bash
rf contracts schema --name hypothesis-design
rf contracts schema --name hypothesis-draft
rf hypothesis draft --file design.json --output /tmp/hf-draft.json
```

The draft output contains a canonical SHA-256 digest. Review its exact contents
before explicitly admitting it to a **frozen campaign**:

```bash
rf hypothesis approve --campaign /tmp/my-rf-campaign \
  --file /tmp/hf-draft.json \
  --expect-sha256 <digest-from-draft-command> --approve-human
```

An explicit approval event is appended **before** the existing
`HYPOTHESIS_PROPOSED` event. Recovery can retry after an interrupted append
without generating an unapproved hypothesis. The service checks the frozen
metric/direction, hypothesis parent and campaign budget, rejects conflicting
IDs, and refuses admission after final nomination. Approval itself does not
execute evaluators, select a candidate or touch the final split.

### Optional model drafts

The only supported live provider in PR11 is OpenAI's chat-completions
endpoint. **Model calls are never made automatically.** Set an API key in the
operator environment only if opting in. The research brief is the complete
outbound prompt context; no campaign directory, dataset, source code, final
split, or secrets are read automatically.

```bash
export OPENAI_API_KEY=... # operator-controlled; never commit keys
rf hypothesis generate --brief /tmp/research-brief.txt \
  --model <operator-selected-model> --allow-model-network \
  --output /tmp/unreviewed-ai-draft.json
```

Generated files have `AI_PROPOSED_UNVERIFIED` status and model/provider plus
prompt/response digests. Model output must conform to the same strict research
design contract, and **cannot approve itself**. The operator must review and
explicitly approve a draft using the command above. Provider availability and
model behavior are not assumed; API failure creates no successful draft.

`--output` uses exclusive creation, not overwrite. Neither drafting command
changes a campaign. Local SHA-256 is an integrity checksum, **not** an
authentication signature or proof that a generated hypothesis is correct.
The trusted local evaluation engine is not a hostile-code sandbox.
Do not put confidential research in a remote brief unless authorized.

## Limits and provenance

- Only review and admission are included; no autonomous hypothesis search,
  model evaluation, automatic Git merge, or manuscript generation.
- A human explicitly approves any model-generated draft. Once admitted, it is
  **proposed**, not `QUALIFIED`, and consumes the existing hypothesis budget.
- Model results are potentially inaccurate, adversarial, or malformed.
  Invalid schema, inconsistent campaign metric and changed digest fail closed.
- The immutable approval event preserves the full draft and its provenance;
  the existing hypothesis event preserves the backwards-compatible executable
  hypothesis identity. Offline ledger checks still apply.
- Repeated qualification is still selection-stage evidence, not a sealed final
  evaluation. The original final split is never read by HypothesisForge.

## Acceptance

`pytest tests/unit/test_hypothesis_forge.py
tests/integration/test_qualification.py`, `ruff`, `mypy`, schema checks,
full `pytest`, and `rf demo run --offline` must pass for PR11 to be merged.

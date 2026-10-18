# PR14 — Fixed-task agent A/B and component ablation evaluation

The PR14 module adds a **real trusted-local subprocess experiment runner** for
agent workflows, with pinned executables, fixed tasks, control and full arms,
leave-one-skill-out ablations, raw answer/log hashes, deterministic schedule and
offline scoring replay. This does not prove general AI-agent uplift.

## Run

~~~bash
# Actual 32 subprocess integration exercise, NOT an LLM study:
python -m researchforge.uplift_fixture_demo --output /tmp/rf-pr14-demo
rf uplift verify --bundle /tmp/rf-pr14-demo
rf uplift report --bundle /tmp/rf-pr14-demo --format md

# Your external trusted agent, predeclared suite and fresh destination:
rf uplift run --plan /path/to/agent-suite.json \
  --output /fresh/agent-evaluation-bundle --approve-trusted-agent
rf uplift verify --bundle /fresh/agent-evaluation-bundle
~~~

A failed, interrupted, malformed, truncated, or absent answer never becomes a
verified pass. Unknown outcomes render affected paired comparisons INCONCLUSIVE.
Explicit approval is mandatory before subprocess execution. Local Python
subprocesses **do not isolate adversarial tools, models, code, network, secrets,
files or other processes**.

## Plan contract

The strict version 1.0 JSON plan requires:

- **schema_version**: "1.0"; **study_id**: a safe unique identifier.
- **provenance_class**: EXTERNAL_AGENT or MECHANICAL_FIXTURE, a declaration
  rather than an external attestation.
- **design_note**, **task_sampling**: preregistered research design and
  limitations of the sampling frame.
- **seed**, **bootstrap_resamples** (200–10000), **timeout_seconds**,
  **max_output_bytes**, **max_total_wall_seconds**: frozen budgets.
- **allowed_environment_keys**: names of optional environment variables
  forwarded to the trusted external tool (never their values in the report).
- **tasks**: each has task_id, family, prompt, expected_answer. Expected
  answers reside in the operator's frozen plan and are NEVER included in
  the agent-visible task JSON. This is not a hostile-process secrecy boundary.
- **skills**: mapping of component name to relative source path and sha256.
  Exact bytes are copied to each trial context directory.
- **arms**: precisely one condition=control with no components, one
  condition=full with all components, and one condition=ablation for
  each component, missing only that component. All arms declare the
  same agent_identity, pinned executable_sha256 and argv, plus optionally
  pinned_files mapping absolute source paths to their SHA-256 digests.

The command uses an absolute executable path and exact separate placeholders
for task, output and context; an optional seed placeholder is supported.
An example trusted agent wrapper arm follows:

~~~json
{
  "name": "full",
  "condition": "full",
  "components": ["experiment", "statistics"],
  "argv": [
    "/absolute/path/to/python",
    "/absolute/path/to/trusted-agent-wrapper.py",
    "--task", "{task}",
    "--output", "{output}",
    "--skills", "{context}",
    "--seed", "{seed}"
  ],
  "executable_sha256": "64_lowercase_hex_bytes_of_python",
  "pinned_files": {
    "/absolute/path/to/trusted-agent-wrapper.py": "64_lowercase_hex_bytes"
  },
  "agent_identity": "provider-model-version-and-policy"
}
~~~

The wrapper writes a JSON object with exactly the string field **answer**
to the output path. ResearchForge compares the actual completed response
against the frozen expected answer. Self-scored agent metrics are never
used. Wrong completed answers and definitively failed processes get zero
credit; unknown answers get neither pass nor fail credit.

## Design and transparency

Keep the external agent version, decoding parameters, prompts, tools, time,
token and cost budgets identical across arms except the *intended* skill
condition. ResearchForge enforces local wall-time, fixed task IDs, executable
byte checks, source hashes, and exposure of the preregistered skill files.
It cannot independently attest a provider's model identity, tokens or billing;
an agent might ignore its context. External APIs, prior session memory,
nondeterminism and task selection can confound results.

The report provides task-paired comparisons from control to full and from
each ablated arm to full, unknown counts, measured pass rates, and
seeded paired percentile-bootstrap intervals. A fixed handpicked dataset is
NOT a representative population; intervals are only descriptive. A genuine
uplift claim requires preregistered representative tasks, sufficient
replications for stochastic agents, independent judges and suitable
uncertainty units (including task families when relevant), and holdout
evaluation independent of adaptive candidate selection.

A completed bundle contains the frozen plan, archived skill bytes,
per-task/arm input/context, captured response, stdout and stderr,
receipt with hashes and reported process state, and JSON/Markdown reports.
Offline verification recomputes original-task answer correctness from
the raw bytes and checks unique trial pairs, argv, all digests and exposed
contexts; it does not launch an agent. Local SHA-256 hashes detect
uncoordinated mutation, not malicious coordinated forgery or fabricated
process execution by an administrator.

**Negative or null findings are explicitly listed**. Unknown trials are
inconclusive. No outcome automatically implies a broad scientific superiority
claim.

## Demonstration versus genuine agent uplift

CI runs the original deterministic fixture: 8 parity/sign tasks times 4
conditions, using 32 real separate Python processes. The intentionally
weaker no-skill control and omitted-rule ablations validate experimental
wiring, not the value of an LLM or ResearchForge in actual research work.
The provenance is MECHANICAL_FIXTURE and the reported population uplift
status is always false.

A real external-agent A/B test is still necessary before stating that
ResearchForge increases actual agent research quality. No model API is
required for the mechanical demo. No license change or modifications to
SatSolv, ADES, private agent configurations or other repositories occur.

# PR13 case studies: real processes, paired evidence and separate correctness oracles

**Status:** Engine + toy demonstrations implemented. No SatSolv-vs-Kissat or
ADES production results are claimed by this PR. The SatSolv default branch
examined on 2026-10-18 contains a bootstrap README, not the evaluated
experimental solver lineage; neither external solver binary was supplied.
An unrelated shortest-path domain is exercised with actual local Python
processes, not a claimed external ADES binary run.

## Contract and CLI

Use a JSON plan that declares two executables, exact SHA-256 of each executable
file, frozen identity/version, dataset cases with relative paths and SHA-256
hashes, family IDs, one common cutoff, seed, output cap, environment note and
research design note.

Run fully independent *toy* case studies (no external services or secrets):

\`\`\`bash
python -m researchforge.case_demo --output /tmp/rf-pr13-case-studies
rf cases verify --bundle /tmp/rf-pr13-case-studies/sat/raw-bundle
rf cases verify --bundle /tmp/rf-pr13-case-studies/shortest_path/raw-bundle
\`\`\`

The command above runs TWO real Python solver subprocesses on each fixture
case in each domain (one correct and one intentionally defective), and writes
raw stdout/stderr for every trial plus frozen inputs, pinned executable
fingerprints, bundle digests, paired outcome/negative evidence, and JSON reports.

To execute your own **trusted** SatSolv and Kissat binaries, create a plan
with \`kind: "sat"\`, \`left.name: "SatSolv"\`,
\`right.name: "Kissat"\` and their real executable paths. Do not substitute
Git branches, labels, or mutable aliases for a pinned binary hash.
Each tool's \`argv\` must contain an absolute executable in position 0 and
exactly one \`"{input}"\` placeholder; optional \`"{seed}"\` can be used.
The fixture's plan under \`sat/plan.json\` illustrates all fields.
Use a fresh output directory and explicit execution approval:

\`\`\`bash
rf cases run --plan /path/to/your/plan.json \
  --output /path/to/new/bundle --approve-trusted-local
rf cases verify --bundle /path/to/new/bundle
\`\`\`

To evaluate another algorithm domain, wrap each ADES-style executable so it
prints a *single JSON object* with exact distances for every node, e.g.
\`{"distances":{"s":0,"a":2,"t":3,"isolated":null}}\`.
Use \`kind: "shortest_path"\` and a graph input
\`{"nodes":["s","a","t"],"source":"s","edges":[["s","a",2],["a","t",3]]}\`.
Inputs are bounded, directed, nonnegative weighted graphs with integer weights,
and the independent verifier uses Bellman-Ford. This is an original interface
adapter; it does **not** modify the ADES repository or claim its native
command-line interface already matches this schema.

## Independent checks and scoring

- DIMACS CNF headers, clauses, variable ranges and file limits are parsed
  independently; SAT assignments are checked against EVERY clause.
- A declared UNSAT result with at most 16 variables is independently verified
  by exhaustive search. Large-instance UNSAT is
  \`UNVERIFIED_UNSAT\`, NOT certified solved, unless a future independently
  checked proof-validation mechanism is implemented.
- Wrong SAT models, false small UNSAT, bad graph distances, inconsistent
  solver exits and malformed/truncated output do **not** receive solved credit.
- Scores are case-ID paired; the report includes certified solved per side,
  SAT/UNSAT category, benchmark family, discordant status, PAR-2-like
  summed seconds (unsolved or invalid = 2 * cutoff), and a seeded paired
  solved-status bootstrap. This bootstrap is descriptive, not statistical
  proof of broad superiority. Hardware scheduling, sample choice and
  multiplicity remain the researcher's responsibility.
- For graph benchmarks, Bellman-Ford independently recomputes distances
  (including disconnected nodes). The two fixture tools produce one
  preserved false answer in each domain.
- The runner executes argv without a shell and with a minimal inherited
  environment, enforces per-trial timeout and output bounds, and separately
  stores SHA-256 of stdout/stderr. Before every run it verifies executable
  bytes match the frozen digest. It never accesses or alters the user's
  SatSolv or ADES repositories.
- Bundle replay is read-only. It requires the exact raw logs and inputs,
  validates digests, reconstructs every correctness result instead of
  trusting the stored verdict, and rejects changed or missing files.
  There is no unattended execution, network access or auto-merge.

## Raw logs and reproduction

CI executes \`python -m researchforge.case_demo\` on Python 3.12 and 3.13,
independently replays each resulting bundle, and publishes
\`researchforge-pr13-case-studies-py*\` **GitHub Actions artifacts** containing
the raw logs, checked input corpus, plans, receipts and verified reports.
They are toy fixture evidence with intentional negative cases, NOT external
SatSolv/Kissat timing data. Artifacts follow GitHub's configured retention,
so archive any real research bundle to durable storage separately.

**For a genuine SatSolv/Kissat study:** pin both source commits, compiler
flags, binaries, CPU/hardware scheduling, benchmark collection/selection and
case hashes; specify CPU-time vs wall-time policy; run a representative
frozen slice and *independently validate UNSAT proofs*. This PR's bounded
exhaustive checker alone does not certify large UNSAT instances. Supply
independent proof logs for an expanded checker interface before counting
those results as verified. A performance superiority claim is **NO-GO**
until those raw runs, independent verification, and statistical qualifications
are available.

This is trusted-local, not a hostile-code security sandbox. A locally mutable
receipt with consistent digests is not independent execution attestation;
replay checks local self-consistency and correctness of small instances.

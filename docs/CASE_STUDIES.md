# PR13 case studies: independent upstream NetworkX and offline correctness replay

## External project selected

**NetworkX** — <https://github.com/networkx/networkx> — is an independently
maintained, BSD-3-Clause-licensed Python network/graph algorithms project.
The case study executes **NetworkX 3.6.1** as a pinned optional dependency.
No code or datasets from the operator's private or personal repositories are
used.

We compare two upstream algorithms on exactly the same directed,
nonnegative-integer weighted graph inputs:

- `networkx.single_source_dijkstra_path_length`
- `networkx.single_source_bellman_ford_path_length`

**Independent result oracle:** ResearchForge's separately implemented
Bellman–Ford checker replays every resulting node distance. Neither NetworkX
arm is treated as the oracle for the other. The internal toy SAT and
shortest-path fixtures from the original PR13 remain as negative-evidence
regression tests, not as the research project's external evidence.

## Reproduction: actual upstream executables

```bash
python -m pip install -e '.[dev,case_studies]'
python -m researchforge.networkx_case_demo --output /tmp/rf-networkx-case
rf cases verify --bundle /tmp/rf-networkx-case/raw-bundle
```

The demo creates a **new directory only** and refuses to overwrite. Each arm
executes an actual separate Python subprocess that imports the installed
NetworkX package, checks `nx.__version__ == "3.6.1"`, calls its named
algorithm, and prints a normalized JSON distances object.

It creates **12 deterministic graph cases** across chain, disconnected,
zero-weight, parallel-edge, directed cycle, and seeded sparse graph families
with sizes up to 30 nodes. Inputs include graph and source identity hashes,
case and family IDs and an independent reference verifier. Correct output
has every node mapped to its integer shortest distance or `null` when
unreachable.

The `rf cases` infrastructure records both complete raw stdout/stderr
logs, subprocess exit codes, bounded wall times, per-case correctness,
original argv, original execution root, Python binary digest, wrapper script
digest, a pinned library version declaration, frozen input digests, seed,
and bundle digest. Raw artifacts are independently verified offline, even
after moving the bundle to a different directory.

The library's wheel/package installation is **version pinned but its
installed package bytes are not independently supply-chain attested**.
`version_or_commit` is a declared identity and does not by itself prove
the origin of an installed package. For high-assurance benchmarking,
add wheel file checksums and reproducible build/environment attestations.

## CI and auditable outputs

GitHub Actions tests on Python 3.12 and 3.13. Each run:

1. Installs optional `networkx==3.6.1`.
2. Executes both upstream algorithms for every frozen graph, yielding
   24 actual processes.
3. Replays the complete raw logs through the separately implemented
   Bellman–Ford oracle.
4. Uploads the plan, raw stdout/stderr, input snapshots, hashes and reports
   under `researchforge-networkx-pr13-py3.12` and
   `researchforge-networkx-pr13-py3.13` artifacts.

The original real-process **toy** fixture demonstration remains in CI as an
adversarial check for bad answers and corrupt bundles.

## Trust, validity and claim boundaries

The exported results are **correctness and integration evidence**, not
evidence that one external algorithm is faster across a meaningful benchmark
population. The corpus is intentionally small and the recorded wall time
includes Python interpreter startup, import costs, and CI scheduling.
The two algorithms belong to the same upstream project and are not
independent organizations or software vendors.

The paired report contains certified solved counts, failures, paired
discordance, per-family scores, PAR-2-like timing totals and descriptive
paired solved-status bootstrap intervals; none alone establishes
population-level or statistically significant algorithm superiority.

The `case-plan` schema and explicit trusted-local command remain available:

```bash
rf contracts schema --name case-plan
rf cases run --plan /path/to/plan.json --output /tmp/unique-output --approve-trusted-local
rf cases verify --bundle /tmp/unique-output
```

Executing arbitrary trusted-local commands is **not a security sandbox**.
The replay validates local self-consistency and mathematical correctness
on these inputs; it is not cryptographic external attestation of runtime
measurement. GitHub artifact availability is subject to workflow retention.

No upstream repository, dependency source, or unrelated project was modified.

# Implementation phases

PR01 typed contracts, CI, schemas and CLI; PR02 append-only ledger/CAS; PR03 frozen
manifest; PR04 trusted-local Git runner; PR05 hypothesis/qualification; PR06 statistical
and untouched final gate; PR07 offline real-subprocess demonstration and portable skill.

The MVP acceptance gate was implemented in PR07. Follow-on roadmap:
PR08 LiteratureForge source adapters; PR09 ResearchAuditor; PR10 independent
portable research skills; PR11 HypothesisForge falsifiable drafts, opt-in model
proposals and explicit human approval; PR12 PaperForge evidence-only exports.

Do not start PR08 literature work until PR07 proves the complete MVP.

## Phase C progress

PR11 HypothesisForge introduces reviewed human/model hypothesis proposals with
explicit admission and bounded attempts. PR12 PaperForge introduces offline,
evidence-closed manuscript draft export with tables from independently verified
final evaluator records and references only from validated literature archives.
PaperForge is not a novelty judge, peer reviewer or publication-ready generator.
PR13 case studies and PR14 agent uplift remain separately gated.

PR13 now includes a reproducible external, independent project case study using
pinned NetworkX 3.6.1 Dijkstra versus Bellman–Ford on 12 deterministic graphs,
independently verified by ResearchForge and CI-archived with complete raw logs.
The existing real-subprocess SAT/graph toy suite remains as negative controls;
no user-owned algorithm or repo is used as the experimental target. These
small executions establish integration and local correctness, not broad
performance superiority or publication readiness.


### PR14 — agent-uplift evaluation harness

Implemented on a separate PR14 branch: immutable fixed-task plan contract, an
opt-in trusted-local shell-free paired agent runner with SHA-pinned source,
control/full/leave-one-out ablation exposure, bounded execution, independent
exact-answer scoring, logged raw outputs, deterministic descriptive intervals
and offline receipt verification. Reference mechanical fixture exercises
32 actual subprocesses in CI but **is not empirical LLM agent uplift**.
A genuine externally sampled and independently scored model-agent campaign,
with controlled provider identity/tokens, remains necessary before any
agent-performance superiority claim. See docs/AGENT_UPLIFT.md.

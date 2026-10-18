# ResearchForge implementation roadmap and current state

This is the implementation ledger for the staged ResearchForge architecture
(RF-ARCH-001). The entries below describe merged source modules, **not** an
external performance or scientific acceptance claim.

| Milestone | Implemented scope | Evidence / guide |
| --- | --- | --- |
| PR01–PR03 | Domain contracts, CLI, schemas, event ledger/CAS and frozen campaign inputs | [Contracts](CONTRACTS.md) |
| PR04–PR07 | Trusted-local evaluator, qualification, one-shot final, statistics, offline demonstration and experiment skill | [MVP acceptance](MVP_ACCEPTANCE.md) |
| PR08 | Opt-in Crossref/OpenAlex metadata retrieval and stored snapshots | [README literature section](../README.md#pr08--literatureforge-v01) |
| PR09–PR10 | Claim/evidence auditor and additional portable research skills | [Audit](RESEARCH_AUDIT.md), [skills](PORTABLE_SKILLS.md) |
| PR11 | HypothesisForge structured drafts and explicit human approval | [HypothesisForge](HYPOTHESIS_FORGE.md) |
| PR12 | PaperForge offline evidence-labeled draft export | [PaperForge](PAPERFORGE.md) |
| PR13 | Deterministic external NetworkX case study and independently replayable raw process evidence | [Case studies](CASE_STUDIES.md) |
| PR14 | Pinned paired-agent control/full/ablation runner with a 32-subprocess mechanical fixture | [Agent uplift](AGENT_UPLIFT.md) |

**PR14 is merged on `main`**; it is not awaiting implementation on a
separate branch. The bundled deterministic agent fixture is deliberately
labeled `MECHANICAL_FIXTURE`, and does **not** show actual LLM uplift.
The external NetworkX exercise verifies paired case-study integration, not
a statistically supported speed or correctness advantage in the wild.

## Next gates, not completed deliverables

1. **Scientific external validity:** Execute independently sampled, frozen
   model-agent tasks with controlled budgets/identity and independent scoring,
   then report uncertainty and negative results. The existing PR14 fixture
   is a harness test, not this study.
2. **Reproducibility beyond CI:** Independently exercise fresh-clone setup,
   packaging, optional integrations and archival replay across environments.
   CI is necessary, not a guarantee of platform-wide reproducibility.
3. **Distribution and legal clearance:** The repository is proprietary
   under [LICENSE](../LICENSE). Obtain written authorization before any
   third-party use or distribution, review dependency notices, and verify
   publication/release artifacts before claiming distribution readiness.
4. **Security boundary:** If untrusted experiment execution becomes a goal,
   separately design and validate isolation. The trusted local runner must
   not be marketed as a sandbox.

These are tracked as unmet gates rather than promised dates or completed
features. See [architecture](ARCHITECTURE.md) for implemented module boundaries.

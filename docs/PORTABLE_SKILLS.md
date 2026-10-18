# PR10 — Three additional independent ResearchForge skills

Original PR07 researchforge-experiment is preserved as the default.
Additional named skills: researchforge-statistics, researchforge-literature,
researchforge-audit. They wrap tested, executable CLI contracts; they do not
introduce a new model dependency or automatic host/plugin configuration.

~~~bash
rf skill validate --name researchforge-statistics
rf skill validate --name researchforge-literature
rf skill validate --name researchforge-audit
rf skill export --name researchforge-audit --target /my/workspace/.agents/skills
~~~

Export creates one new directory and never overwrites installed skills.
Symlinked path components are refused. The original no-name CLI commands
continue working. No ResearchForge license has been assigned.

External scholarly provider calls require the separate --allow-network switch
and operator permission. The offline auditor only verifies typed mechanical
predicates, never arbitrary claim prose or universal scientific validity.

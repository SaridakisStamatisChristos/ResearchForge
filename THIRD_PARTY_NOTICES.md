# Third-party notices and license status

ResearchForge depends on external packages declared in `pyproject.toml`
(including Pydantic and Typer; optional HTTPX and NetworkX; testing and
tooling dependencies in the `dev` extra). Each dependency retains its own
upstream copyright and license terms. This file does **not** substitute for
checking exact installed package versions and their notices before distribution.

The NetworkX example calls the optional external NetworkX package at runtime;
it is not a ResearchForge benchmark result establishing performance
superiority. See [case study documentation](docs/CASE_STUDIES.md).
The original PR01 provenance note reports no copied K-Dense-AI source; see
[upstream adaptations](docs/UPSTREAM_ADAPTATIONS.md). Do not infer from that
historical note that all future provenance and transitive license requirements
have been exhaustively audited.

**ResearchForge is proprietary (All Rights Reserved).** The governing terms
are in [LICENSE](LICENSE); this notices file does not grant rights to use,
modify, publish, sell or redistribute ResearchForge. Its public visibility
does not make it open source. Review the licenses and notices for all
third-party dependencies before any authorized distribution.

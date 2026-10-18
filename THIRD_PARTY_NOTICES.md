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

**ResearchForge has no selected repository license.** This file grants no
permission to reuse, modify, publish or redistribute ResearchForge source.
A maintainer licensing decision and dependency-notice review are required
before describing it as an open-source or distributable package.

---
name: researchforge-literature
description: Look up source-specific Crossref and OpenAlex scholarly metadata with opt-in network and immutable offline retrieval snapshots.
compatibility: Python 3.12+ and ResearchForge CLI with optional literature extra; live network calls need explicit operator consent.
metadata:
  version: "0.1.0"
---

# Literature skill

Only retrieve live provider data with explicit operator network approval.

~~~bash
python -m pip install -e '.[literature]'
rf literature lookup --doi 10.1038/nphys1170 --sources crossref,openalex --allow-network --store /tmp/rf-lit --json
rf literature search --query "dynamic shortest paths" --sources openalex --limit 20 --allow-network --store /tmp/rf-lit --json
rf literature report --search-id ACTUAL_SNAPSHOT_HASH --store /tmp/rf-lit --format md
~~~

Use the exact search_id returned by the retrieval rather than the placeholder.
The report command is offline and validates the stored source snapshots.
Record provider, timestamps, paging limits, hashes and COMPLETE/PARTIAL/UNKNOWN
statuses. NOT_FOUND applies only to the queried source at that time.
DOI equality may group source records but never silently reconcile titles,
authors or citation counts. Source appearance is not peer review or proof of
scholarly correctness, novelty or universal discoverability. A SKILL.md is
instructions, not network permission.

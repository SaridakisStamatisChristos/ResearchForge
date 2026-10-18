---
name: researchforge-statistics
description: Compute deterministic paired benchmark summaries with seeded uncertainty and explicit limitations from real measurements.
compatibility: Python 3.12+ and ResearchForge CLI; statistical inspection is offline.
metadata:
  version: "0.1.0"
---

# Statistics skill

Use measured observations with identical distinct case IDs in both baseline and
candidate maps. Declare direction as min or max. Do not fabricate measurements.

Example observations.json:

~~~json
{"direction":"min","baseline":{"a":12,"b":10},"candidate":{"a":9,"b":11}}
~~~

Run the actual deterministic comparison:

~~~bash
rf stats paired --file observations.json --seed 1701 --resamples 2000
~~~

Preserve inputs, seed and returned interval. A bootstrap on selected cases is
exploratory and does not prove general population superiority. Do not treat
qualification as an untouched final test or silently discard timeouts. The
skill is instructions only; it does not grant execution authority.

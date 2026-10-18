# ADR-0001 — Modular monolith

**Accepted:** one Python package separates pure domain models, application decisions,
ports, adapters and CLI. No network or LLM needed for deterministic core operations.
Revisit distribution only after measured need.

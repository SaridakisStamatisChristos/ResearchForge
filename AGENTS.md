# Development contract

Follow RF-ARCH-001 and its invariants I-01–I-10. Keep PR boundaries and execute the
smallest independently verifiable slice. PR01 does not claim evaluator or ledger behavior.
Do not invent experiment results, change repository licensing without explicit
owner authorization, modify other repositories, execute untrusted code, or
equate selection-stage qualification to final evidence.

Run `python tools/generate_schemas.py --check`, `pytest`, `ruff check .`,
`ruff format --check .` and `mypy src` before asserting a green PR. When tools are unavailable,
report the missing checks instead of declaring them passed.

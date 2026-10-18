"""Backward-compatible ResearchForge CLI with the PR14 paired-study commands."""

from researchforge.cli import app
from researchforge.uplift import uplift_commands

app.add_typer(uplift_commands, name="uplift")

# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Commands

```bash
uv sync                        # Install dependencies + project in editable mode
uv run proton-to-icloud --help # Run the CLI
uv run pytest                  # Run tests
uv run ruff check src/ tests/  # Lint
uv run ruff format src/ tests/ # Format
uv build                       # Build sdist + wheel
```

## Architecture

Zero-dependency Python CLI tool that uploads Proton Mail .eml exports to iCloud Mail via IMAP.

**Key structure:**
- `src/proton_to_icloud/cli.py` — argparse CLI with `upload` and `batch` subcommands
- `src/proton_to_icloud/upload.py` — IMAP APPEND upload logic with resume/state
- `src/proton_to_icloud/batch.py` — Split .eml files into numbered batch folders
- `src/proton_to_icloud/progress.py` — Terminal progress bar utilities

**Design principles:**
- Zero external dependencies (stdlib only) — no dependency conflicts when installed globally
- Uses argparse, not click/typer
- src/ layout with hatchling build backend, managed by uv
- Python >=3.11

## Linting

- Ruff, line-length 100
- Rules: B, C4, C90, E, F, I, W

# Contributing

Thanks for your interest in contributing! This project is maintained on a
best-effort basis, so response times may vary.

## Quick start

```bash
git clone https://github.com/abuchmueller/proton-to-icloud.git
cd proton-to-icloud
uv sync
uv run pytest
```

## Before submitting a PR

1. Run `uv run ruff check src/ tests/` and `uv run ruff format src/ tests/`
2. Run `uv run pytest` — all tests must pass
3. Keep changes focused — one fix or feature per PR
4. Zero external dependencies — stdlib only for the main package

## Reporting bugs

Open an issue with:
- The command you ran
- The error output
- Your Python version (`python3 --version`)

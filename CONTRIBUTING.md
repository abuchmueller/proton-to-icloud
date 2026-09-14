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

## Releasing (maintainers)

The version is single-sourced from `pyproject.toml`; `proton_to_icloud.__version__`
reads it from the installed package metadata. Publishing to PyPI happens
automatically through [trusted publishing](https://docs.pypi.org/trusted-publishers/)
when a GitHub release is *published* (`.github/workflows/publish.yml`, `pypi`
environment) — there are no API tokens to manage.

1. Make sure everything that belongs in the release is merged into `main`.
2. Bump the version — this updates `pyproject.toml` and `uv.lock` together:

   ```bash
   uv version --bump minor     # new features (pre-1.0: also behaviour changes)
   uv version --bump patch     # bug fixes only
   uv version 0.2.0            # or set an explicit version
   ```

3. Sanity-check the build and tests:

   ```bash
   rm -rf dist && uv build     # dist/ must contain proton_to_icloud-<new>-py3-none-any.whl + .tar.gz
   uv run pytest               # includes a check that __version__ matches pyproject.toml
   ```

4. Commit, open a PR, wait for CI, merge. (Your own PRs need the admin bypass,
   since `main` requires one approving review and you cannot approve yourself.)
5. Publish the release. This creates the tag on `main` and triggers the publish
   workflow; draft releases do **not** trigger it:

   ```bash
   gh release create v<new> --target main --generate-notes
   ```

6. Verify (the workflow takes about 30 seconds):

   ```bash
   gh run watch
   curl -s https://pypi.org/pypi/proton-to-icloud/json | python3 -c 'import json,sys; print(json.load(sys.stdin)["info"]["version"])'
   uv tool install proton-to-icloud@<new> && proton-to-icloud --version
   ```

PyPI releases are immutable: a broken upload cannot be replaced, only superseded
by a new patch version — hence step 3.

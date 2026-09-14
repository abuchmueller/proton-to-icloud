"""Tests for the package version — single-sourced from pyproject.toml."""

import tomllib
from pathlib import Path

import proton_to_icloud


def test_version_matches_pyproject():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with open(pyproject, "rb") as f:
        expected = tomllib.load(f)["project"]["version"]
    assert proton_to_icloud.__version__ == expected

"""proton-to-icloud: Migrate Proton Mail exports to iCloud Mail."""

from importlib.metadata import PackageNotFoundError, version

# Single source of truth is pyproject.toml; read it from the installed metadata.
try:
    __version__ = version("proton-to-icloud")
except PackageNotFoundError:  # source tree that was never installed
    __version__ = "0.0.0+unknown"

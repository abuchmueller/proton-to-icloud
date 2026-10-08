"""Proton Mail metadata parsing and folder routing logic.

Reads ``labels.json`` and per-message ``.metadata.json`` files from a Proton
Mail export and resolves each email to the correct IMAP target folder.
"""

from __future__ import annotations

import email.utils
import json
import os
from datetime import datetime
from email.parser import BytesHeaderParser

# ── Constants ────────────────────────────────────────────────────────────────

# Meta-labels that don't correspond to real IMAP folders — always skip.
SKIP_LABEL_IDS: frozenset[str] = frozenset({"1", "2", "5", "9", "10", "12", "15", "16"})

# Highest-priority first: Inbox > Sent > Drafts > Spam > Trash > Archive.
LABEL_PRIORITY: list[str] = ["0", "7", "8", "4", "3", "6"]

# Proton folder name → iCloud native IMAP name (used with --direct).
ICLOUD_FOLDER_MAP: dict[str, str] = {
    "Inbox": "INBOX",
    "Sent": "Sent Messages",
    "Drafts": "Drafts",
    "Spam": "Junk",
    "Trash": "Deleted Messages",
    "Archive": "Archive",
}


# ── Label loading ────────────────────────────────────────────────────────────


def load_labels(source_dir: str) -> dict[str, str] | None:
    """Find and parse ``labels.json``, returning ``{id: name}`` or *None*.

    Searches *source_dir*, then ``source_dir/json/``, then the parent directory.
    """
    candidates = [
        os.path.join(source_dir, "labels.json"),
        os.path.join(source_dir, "json", "labels.json"),
        os.path.join(os.path.dirname(os.path.abspath(source_dir)), "labels.json"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            try:
                with open(path) as f:
                    data = json.load(f)
                payload = data.get("Payload", [])
                return {item["ID"]: item["Name"] for item in payload}
            except (OSError, json.JSONDecodeError, KeyError, TypeError):
                # Intentional: a corrupted file should not silently fall through
                # to the next candidate path — surface the problem immediately.
                return None
    return None


# ── Per-message metadata ─────────────────────────────────────────────────────


def _find_metadata_path(eml_path: str) -> str | None:
    """Return the ``.metadata.json`` path belonging to *eml_path*, or *None*.

    Handles both flat exports (``foo.eml`` → ``foo.metadata.json``) and
    split exports (``eml/foo.eml`` → ``../json/foo.metadata.json``).
    """
    base, _ = os.path.splitext(eml_path)

    # Flat layout: sibling file
    sibling = base + ".metadata.json"
    if os.path.isfile(sibling):
        return sibling

    # Split layout: eml/ and json/ directories
    directory = os.path.dirname(eml_path)
    parent = os.path.dirname(directory)
    filename = os.path.basename(base) + ".metadata.json"
    json_path = os.path.join(parent, "json", filename)
    if os.path.isfile(json_path):
        return json_path

    return None


def read_label_ids(eml_path: str) -> list[str] | None:
    """Return ``LabelIDs`` from the ``.metadata.json`` belonging to *eml_path*."""
    metadata_path = _find_metadata_path(eml_path)
    if metadata_path is None:
        return None
    return _parse_label_ids(metadata_path)


def _parse_label_ids(metadata_path: str) -> list[str] | None:
    """Read a single metadata JSON file and extract LabelIDs."""
    try:
        with open(metadata_path) as f:
            data = json.load(f)
        return data["Payload"]["LabelIDs"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None


# ── Received time / date filtering ───────────────────────────────────────────

# Enough to cover the header block of any sane email without reading attachments.
_HEADER_READ_LIMIT = 256 * 1024


def read_received_time(eml_path: str) -> float | None:
    """Return when the email at *eml_path* was received, as a Unix timestamp.

    Prefers Proton's ``Payload.Time`` from the ``.metadata.json`` file (the
    server receive time) and falls back to the ``Date:`` header of the
    ``.eml`` itself.  File-system timestamps are never used — they reflect
    when the export ran, not when the email arrived.
    """
    metadata_path = _find_metadata_path(eml_path)
    if metadata_path is not None:
        try:
            with open(metadata_path) as f:
                value = json.load(f)["Payload"]["Time"]
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                return float(value)
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            pass

    return _read_date_header(eml_path)


def _read_date_header(eml_path: str) -> float | None:
    """Parse the ``Date:`` header of *eml_path* into a Unix timestamp."""
    try:
        with open(eml_path, "rb") as f:
            head = f.read(_HEADER_READ_LIMIT)
        date_value = BytesHeaderParser().parsebytes(head).get("Date")
        if not date_value:
            return None
        parsed = email.utils.parsedate_tz(str(date_value))
        if parsed is None:
            return None
        return float(email.utils.mktime_tz(parsed))
    except Exception:
        return None


def filter_since(eml_files: list[str], since: datetime) -> tuple[list[str], int, list[str]]:
    """Keep only emails received at or after *since*.

    *since* must be timezone-aware.  Returns ``(kept, excluded_count,
    undated)`` where *undated* lists files whose receive time could not be
    determined — these are **kept** (and also included in *kept*) so that no
    potentially-new email is silently dropped.
    """
    cutoff = since.timestamp()
    kept: list[str] = []
    undated: list[str] = []
    excluded = 0
    for path in eml_files:
        received = read_received_time(path)
        if received is None:
            undated.append(path)
            kept.append(path)
        elif received >= cutoff:
            kept.append(path)
        else:
            excluded += 1
    return kept, excluded, undated


# ── Folder resolution ────────────────────────────────────────────────────────


def resolve_target_folder(
    label_ids: list[str] | None,
    labels_map: dict[str, str] | None,
    *,
    direct: bool,
    base_mailbox: str,
) -> str:
    """Pick the IMAP target folder for one email based on its labels.

    *direct=True* maps to native iCloud folder names; *direct=False* creates
    subfolders under *base_mailbox*.  Falls back to *base_mailbox* when the
    label is unknown or missing.
    """
    if not label_ids or not labels_map:
        return base_mailbox

    # Filter out meta-labels
    real = [lid for lid in label_ids if lid not in SKIP_LABEL_IDS]
    if not real:
        return base_mailbox

    # Pick highest-priority label
    chosen_id: str | None = None
    for priority_id in LABEL_PRIORITY:
        if priority_id in real:
            chosen_id = priority_id
            break

    if chosen_id is None:
        return base_mailbox

    label_name = labels_map.get(chosen_id)
    if not label_name:
        return base_mailbox

    if direct:
        return ICLOUD_FOLDER_MAP.get(label_name, base_mailbox)

    return f"{base_mailbox}/{label_name}"


# ── Routing plan ─────────────────────────────────────────────────────────────


def build_routing_plan(
    eml_files: list[str],
    source_dir: str,
    *,
    direct: bool,
    base_mailbox: str,
) -> dict[str, list[str]]:
    """Group *eml_files* by their resolved target IMAP folder.

    Returns ``{folder_name: [eml_path, ...]}``.  If ``labels.json`` is not
    found, every file is placed in *base_mailbox* (backward-compatible).
    """
    labels_map = load_labels(source_dir)

    if labels_map is None:
        return {base_mailbox: list(eml_files)}

    routing: dict[str, list[str]] = {}
    for path in eml_files:
        label_ids = read_label_ids(path)
        folder = resolve_target_folder(
            label_ids, labels_map, direct=direct, base_mailbox=base_mailbox
        )
        routing.setdefault(folder, []).append(path)

    return routing


def print_routing_summary(routing: dict[str, list[str]]) -> None:
    """Print a table of target folders and email counts."""
    print()
    print("Routing plan:")
    for folder in sorted(routing, key=lambda f: (-len(routing[f]), f)):
        count = len(routing[folder])
        print(f"  {folder:<24} {count:>7,} emails")
    print()

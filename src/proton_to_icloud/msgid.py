"""Message-ID helpers for skipping emails already present in iCloud.

Matching is by RFC 5322 Message-ID only. Emails without a Message-ID are
uploaded (we cannot prove they are duplicates). Trash, Junk, and Notes are
ignored by default so a Proton copy is not dropped just because iCloud still
has a deleted or spam copy.
"""

from __future__ import annotations

import email.parser
import email.policy
import imaplib
import sys
import time
from typing import NamedTuple

from proton_to_icloud.imap_names import decode_imap_utf7, quote_wire_name

# iCloud folders that should not block an import.
DEFAULT_SKIP_FOLDERS: frozenset[str] = frozenset(
    {
        "deleted messages",
        "junk",
        "notes",
    }
)

# SPECIAL-USE flags (RFC 6154) of folders that should not block an import.
SKIP_SPECIAL_USE: frozenset[str] = frozenset({"\\Trash", "\\Junk"})

FETCH_BATCH = 200
_FETCH_SLEEP = 0.05


def normalize_message_id(value: str | None) -> str | None:
    """Return a comparable Message-ID, or None if *value* is empty."""
    if value is None:
        return None
    s = " ".join(str(value).split()).strip()
    if not s:
        return None
    if len(s) >= 2 and s.startswith("<") and s.endswith(">"):
        s = s[1:-1]
    s = s.strip().lower()
    return s or None


def extract_message_id_from_eml(raw: bytes) -> str | None:
    """Parse the Message-ID header from raw EML (or a header fragment)."""
    try:
        msg = email.parser.BytesParser(policy=email.policy.default).parsebytes(
            raw, headersonly=True
        )
    except Exception:
        return None
    return normalize_message_id(msg.get("Message-ID"))


class Mailbox(NamedTuple):
    """One IMAP LIST entry: *wire* is what the server expects back, *display* is for humans."""

    flags: frozenset[str]
    wire: str
    display: str


def parse_imap_list_line(line: str) -> Mailbox | None:
    """Parse one IMAP LIST line into a :class:`Mailbox`."""
    line = line.strip()
    if not line.startswith("("):
        return None

    depth = 0
    flags_end: int | None = None
    for i, ch in enumerate(line):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                flags_end = i
                break
    if flags_end is None:
        return None

    flags = frozenset(line[1:flags_end].split())
    rest = line[flags_end + 1 :].strip()
    if not rest:
        return None

    if rest.startswith('"'):
        delim_end = rest.find('"', 1)
        if delim_end < 0:
            return None
        mailbox = rest[delim_end + 1 :].strip()
    else:
        parts = rest.split(None, 1)
        mailbox = parts[-1] if parts else ""

    if len(mailbox) >= 2 and mailbox.startswith('"') and mailbox.endswith('"'):
        mailbox = mailbox[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if not mailbox:
        return None
    return Mailbox(flags, mailbox, decode_imap_utf7(mailbox))


def parse_message_ids_from_fetch(fetched: list | None) -> set[str]:
    """Extract normalized Message-IDs from an IMAP FETCH response."""
    ids: set[str] = set()
    if not fetched:
        return ids
    for item in fetched:
        if item is None:
            continue
        payload: bytes | None = None
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            payload = item[1]
        elif isinstance(item, bytes):
            payload = item
        if payload is None:
            continue
        msgid = extract_message_id_from_eml(payload)
        if msgid:
            ids.add(msgid)
    return ids


def classify_eml_files(
    eml_files: list[str], existing_ids: set[str], header_bytes: int = 16384
) -> tuple[int, int, int]:
    """Count how many files would upload vs skip vs lack a Message-ID.

    Returns ``(would_upload, would_skip, missing_msgid)``. Intra-export
    duplicates (same Message-ID appearing twice) count as skip.
    """
    seen = set(existing_ids)
    would_upload = 0
    would_skip = 0
    missing = 0
    for path in eml_files:
        try:
            with open(path, "rb") as f:
                raw = f.read(header_bytes)
        except OSError:
            would_upload += 1
            continue
        msgid = extract_message_id_from_eml(raw)
        if msgid is None:
            missing += 1
            would_upload += 1
            continue
        if msgid in seen:
            would_skip += 1
            continue
        seen.add(msgid)
        would_upload += 1
    return would_upload, would_skip, missing


def list_selectable_mailboxes(conn: imaplib.IMAP4_SSL) -> list[Mailbox]:
    """Return selectable IMAP mailboxes, skipping \\Noselect."""
    status, data = conn.list()
    names: list[Mailbox] = []
    if status != "OK" or data is None:
        return names
    for item in data:
        if item is None:
            continue
        line = item.decode("utf-8", errors="replace") if isinstance(item, bytes) else str(item)
        parsed = parse_imap_list_line(line)
        if parsed is None:
            continue
        if "\\Noselect" in parsed.flags or "\\NonExistent" in parsed.flags:
            continue
        names.append(parsed)
    return names


def fetch_message_ids_from_mailbox(conn: imaplib.IMAP4_SSL, wire_name: str) -> set[str]:
    """FETCH Message-ID headers for every message in a mailbox (read-only).

    *wire_name* is the name exactly as returned by LIST (modified UTF-7).
    """
    status, _ = conn.select(quote_wire_name(wire_name), readonly=True)
    if status != "OK":
        return set()

    status, data = conn.uid("SEARCH", None, "ALL")
    if status != "OK" or not data or data[0] is None or data[0] == b"":
        return set()

    uids = data[0].split()
    if not uids:
        return set()

    ids: set[str] = set()
    for i in range(0, len(uids), FETCH_BATCH):
        batch = b",".join(uids[i : i + FETCH_BATCH]).decode("ascii")
        status, fetched = conn.uid(
            "FETCH",
            batch,
            "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])",
        )
        if status == "OK":
            ids.update(parse_message_ids_from_fetch(fetched))
        if i + FETCH_BATCH < len(uids):
            time.sleep(_FETCH_SLEEP)
    return ids


def collect_existing_message_ids(
    conn: imaplib.IMAP4_SSL,
    skip_folders: frozenset[str] | set[str] | None = None,
) -> set[str]:
    """Scan iCloud folders and return the set of normalized Message-IDs."""
    folders = skip_folders if skip_folders is not None else DEFAULT_SKIP_FOLDERS
    skip = {s.casefold() for s in folders}
    mailboxes = list_selectable_mailboxes(conn)
    if not mailboxes:
        print("  WARNING: IMAP LIST returned no selectable folders.", file=sys.stderr)

    all_ids: set[str] = set()
    for mailbox in mailboxes:
        if mailbox.flags & SKIP_SPECIAL_USE or mailbox.display.casefold() in skip:
            print(f"  Ignoring '{mailbox.display}' (not used for duplicate matching).")
            continue
        sys.stdout.write(f"  Scanning '{mailbox.display}' ...")
        sys.stdout.flush()
        try:
            ids = fetch_message_ids_from_mailbox(conn, mailbox.wire)
        except (imaplib.IMAP4.error, imaplib.IMAP4.abort, OSError) as e:
            print(f" failed ({e})")
            continue
        all_ids.update(ids)
        print(f" {len(ids):,} Message-IDs")
    return all_ids

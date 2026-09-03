"""Message-ID helpers for skipping emails already present in iCloud.

Matching is by RFC 5322 Message-ID only. Emails without a Message-ID are
uploaded (we cannot prove they are duplicates). Trash, Junk, and Notes are
ignored by default so a Proton copy is not dropped just because iCloud still
has a deleted or spam copy.
"""

from __future__ import annotations

import base64
import email.parser
import email.policy
import imaplib
import re
import sys
import time

# iCloud folders that should not block an import.
DEFAULT_SKIP_FOLDERS: frozenset[str] = frozenset(
    {
        "deleted messages",
        "junk",
        "notes",
    }
)

FETCH_BATCH = 200
_FETCH_SLEEP = 0.05

_IMAP_UTF7_RE = re.compile(r"&([^-]*)-")


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


def decode_imap_utf7(name: str) -> str:
    """Decode an IMAP modified UTF-7 mailbox name."""

    def _repl(match: re.Match[str]) -> str:
        token = match.group(1)
        if token == "":
            return "&"
        b64 = token.replace(",", "/")
        pad = (-len(b64)) % 4
        try:
            return base64.b64decode(b64 + "=" * pad).decode("utf-16-be")
        except Exception:
            return match.group(0)

    return _IMAP_UTF7_RE.sub(_repl, name)


def parse_imap_list_line(line: str) -> tuple[frozenset[str], str] | None:
    """Parse one IMAP LIST line into ``(flags, mailbox_name)``."""
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
        mailbox = mailbox[1:-1]
    mailbox = decode_imap_utf7(mailbox)
    if not mailbox:
        return None
    return flags, mailbox


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


def _quote_mailbox(name: str) -> str:
    if " " in name:
        return f'"{name}"'
    return name


def list_selectable_mailboxes(conn: imaplib.IMAP4_SSL) -> list[str]:
    """Return selectable IMAP mailbox names, skipping \\Noselect."""
    status, data = conn.list()
    names: list[str] = []
    if status != "OK" or data is None:
        return names
    for item in data:
        if item is None:
            continue
        line = item.decode("utf-8", errors="replace") if isinstance(item, bytes) else str(item)
        parsed = parse_imap_list_line(line)
        if parsed is None:
            continue
        flags, name = parsed
        if "\\Noselect" in flags or "\\NonExistent" in flags:
            continue
        names.append(name)
    return names


def fetch_message_ids_from_mailbox(conn: imaplib.IMAP4_SSL, mailbox: str) -> set[str]:
    """FETCH Message-ID headers for every message in *mailbox* (read-only)."""
    status, _ = conn.select(_quote_mailbox(mailbox), readonly=True)
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
        if mailbox.casefold() in skip:
            print(f"  Ignoring '{mailbox}' (not used for duplicate matching).")
            continue
        sys.stdout.write(f"  Scanning '{mailbox}' ...")
        sys.stdout.flush()
        try:
            ids = fetch_message_ids_from_mailbox(conn, mailbox)
        except (imaplib.IMAP4.error, imaplib.IMAP4.abort, OSError) as e:
            print(f" failed ({e})")
            continue
        all_ids.update(ids)
        print(f" {len(ids):,} Message-IDs")
    return all_ids

"""IMAP mailbox-name helpers: modified UTF-7 (RFC 3501 §5.1.3) and quoting.

``imaplib`` sends command arguments as-is and encodes them as ASCII, so a
mailbox name must be converted to its *wire* form before use: non-ASCII
characters go through modified UTF-7, and names with spaces, quotes or
backslashes become a quoted string.
"""

from __future__ import annotations

import base64
import re

_UTF7_SEGMENT_RE = re.compile(r"&([^-]*)-")
_NEEDS_QUOTING_RE = re.compile(r'[\s"\\(){%*\]]')


def encode_imap_utf7(name: str) -> str:
    """Encode a human-readable mailbox name to IMAP modified UTF-7."""
    out: list[str] = []
    pending: list[str] = []

    def flush() -> None:
        if pending:
            raw = "".join(pending).encode("utf-16-be")
            out.append(
                "&" + base64.b64encode(raw).decode("ascii").rstrip("=").replace("/", ",") + "-"
            )
            pending.clear()

    for ch in name:
        if 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            pending.append(ch)
    flush()
    return "".join(out)


def decode_imap_utf7(name: str) -> str:
    """Decode an IMAP modified UTF-7 mailbox name for display."""

    def _repl(match: re.Match[str]) -> str:
        token = match.group(1)
        if token == "":
            return "&"
        b64 = token.replace(",", "/")
        try:
            return base64.b64decode(b64 + "=" * (-len(b64) % 4)).decode("utf-16-be")
        except Exception:
            return match.group(0)

    return _UTF7_SEGMENT_RE.sub(_repl, name)


def quote_wire_name(wire_name: str) -> str:
    """Quote an already-encoded (ASCII) mailbox name if IMAP syntax requires it."""
    if wire_name and not _NEEDS_QUOTING_RE.search(wire_name):
        return wire_name
    escaped = wire_name.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def mailbox_arg(name: str) -> str:
    """Turn a human-readable mailbox name into an ``imaplib`` command argument."""
    return quote_wire_name(encode_imap_utf7(name))

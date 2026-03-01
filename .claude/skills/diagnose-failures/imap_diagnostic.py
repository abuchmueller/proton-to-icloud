#!/usr/bin/env python3
"""Diagnose iCloud IMAP upload failures for proton-to-icloud.

Reads failed files from .imap_upload_state.json and runs a systematic
8-test diagnostic matrix against iCloud's IMAP server to identify why
each file was rejected.

Usage:
    python3 imap_diagnostic.py <source-dir> <email> [password]

If password is omitted, it is prompted securely.
"""

import email.utils
import getpass
import imaplib
import json
import os
import sys
import time

IMAP_HOST = "imap.mail.me.com"
IMAP_PORT = 993
DIAG_MAILBOX = "Diagnostic-Test"
STATE_FILENAME = ".imap_upload_state.json"


# ── Helpers ──────────────────────────────────────────────────────────────────


def load_failed_files(source_dir: str) -> list[str]:
    """Auto-discover failed files from the state file."""
    state_path = os.path.join(source_dir, STATE_FILENAME)
    if not os.path.isfile(state_path):
        return []
    try:
        with open(state_path) as f:
            data = json.load(f)
        return [p for p in data.get("failed_files", []) if os.path.isfile(p)]
    except (OSError, json.JSONDecodeError):
        return []


def parse_date(raw: bytes) -> str | None:
    """Extract the Date header and convert to IMAP internaldate."""
    try:
        text = raw.decode("utf-8", errors="replace")
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped.lower().startswith("date:"):
                date_str = stripped[5:].strip()
                parsed = email.utils.parsedate_tz(date_str)
                if parsed:
                    timestamp = email.utils.mktime_tz(parsed)
                    return imaplib.Time2Internaldate(timestamp)
        return None
    except Exception:
        return None


def sanitize_headers(raw: bytes) -> bytes:
    """Remove empty-value headers and replace non-ASCII bytes in headers."""
    for sep in (b"\r\n\r\n", b"\n\n"):
        pos = raw.find(sep)
        if pos >= 0:
            break
    else:
        return raw

    header_section = raw[:pos]
    rest = raw[pos:]

    line_end = b"\r\n" if b"\r\n" in header_section else b"\n"
    lines = header_section.split(line_end)

    # Strip empty-value headers
    cleaned: list[bytes] = []
    for line in lines:
        if b":" in line and not line.startswith((b" ", b"\t")):
            _, _, value = line.partition(b":")
            if value.strip() == b"":
                continue
        cleaned.append(line)

    header_section = line_end.join(cleaned)

    # Replace non-ASCII bytes with '?'
    if any(b > 127 for b in header_section):
        header_section = bytes(b if b <= 127 else ord(b"?") for b in header_section)

    return header_section + rest


def make_minimal(raw: bytes) -> bytes:
    """Create a minimal message preserving only Subject, Date, From, To, and body."""
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        return raw

    for sep in ("\r\n\r\n", "\n\n"):
        pos = text.find(sep)
        if pos >= 0:
            body = text[pos:]
            header_text = text[:pos]
            break
    else:
        return raw

    line_end = "\r\n" if "\r\n" in header_text else "\n"
    keep_headers = {"subject", "date", "from", "to", "content-type", "mime-version"}

    # Unfold continuation lines
    unfolded: list[str] = []
    for line in header_text.split(line_end):
        if line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += " " + line.strip()
        else:
            unfolded.append(line)

    kept: list[str] = []
    for line in unfolded:
        if ":" in line:
            name = line.split(":")[0].lower()
            if name in keep_headers:
                kept.append(line)

    return (line_end.join(kept) + body).encode("utf-8", errors="replace")


def analyze_headers(raw: bytes) -> dict:
    """Analyze a message's headers for known issues."""
    issues: dict = {"empty_headers": [], "non_ascii": False, "bad_date": False, "defects": []}

    for sep in (b"\r\n\r\n", b"\n\n"):
        pos = raw.find(sep)
        if pos >= 0:
            break
    else:
        return issues

    header_bytes = raw[:pos]

    # Check non-ASCII
    if any(b > 127 for b in header_bytes):
        issues["non_ascii"] = True

    # Check empty-value headers
    line_end = b"\r\n" if b"\r\n" in header_bytes else b"\n"
    for line in header_bytes.split(line_end):
        if b":" in line and not line.startswith((b" ", b"\t")):
            name, _, value = line.partition(b":")
            if value.strip() == b"":
                issues["empty_headers"].append(name.decode("utf-8", errors="replace"))

    # Check date
    if parse_date(raw) is None:
        issues["bad_date"] = True

    # Check MIME defects
    try:
        import email as email_mod

        msg = email_mod.message_from_bytes(raw)
        if msg.defects:
            issues["defects"] = [str(d) for d in msg.defects]
    except Exception:
        pass

    return issues


# ── Diagnostic matrix ────────────────────────────────────────────────────────

TESTS = [
    ("original + date", False, False, True),
    ("original + no date", False, False, False),
    ("sanitized + date", True, False, True),
    ("sanitized + no date", True, False, False),
    ("sanitized + \\Seen + date", True, True, True),
    ("sanitized + \\Seen + no date", True, True, False),
    ("minimal + date", "minimal", False, True),
    ("minimal + no date", "minimal", False, False),
]


def run_test(
    conn: imaplib.IMAP4_SSL,
    raw: bytes,
    *,
    do_sanitize,
    add_seen: bool,
    with_date: bool,
    mailbox: str,
) -> tuple[bool, str]:
    """Run a single APPEND test. Returns (success, detail)."""
    if do_sanitize == "minimal":
        msg = make_minimal(raw)
    elif do_sanitize:
        msg = sanitize_headers(raw)
    else:
        msg = raw

    date = parse_date(msg) if with_date else None
    flags = r"\Seen" if add_seen else ""

    quoted = f'"{mailbox}"' if " " in mailbox else mailbox

    try:
        status, response = conn.append(quoted, flags, date, msg)
        if status == "OK":
            return True, "OK"
        detail = str(response)
        return False, detail
    except Exception as e:
        return False, str(e)


def diagnose_file(
    conn: imaplib.IMAP4_SSL,
    filepath: str,
    mailbox: str,
) -> dict:
    """Run the full diagnostic matrix on one .eml file."""
    try:
        with open(filepath, "rb") as f:
            raw = f.read()
    except OSError as e:
        return {"file": filepath, "error": f"Cannot read: {e}", "tests": {}}

    results: dict = {
        "file": os.path.basename(filepath),
        "size": len(raw),
        "issues": analyze_headers(raw),
        "tests": {},
    }

    for name, do_sanitize, add_seen, with_date in TESTS:
        success, detail = run_test(
            conn,
            raw,
            do_sanitize=do_sanitize,
            add_seen=add_seen,
            with_date=with_date,
            mailbox=mailbox,
        )
        results["tests"][name] = {"pass": success, "detail": detail}
        time.sleep(0.1)

    return results


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Diagnose iCloud IMAP upload failures for proton-to-icloud.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python3 imap_diagnostic.py /path/to/mail_export you@icloud.com
  python3 imap_diagnostic.py /path/to/mail_export you@icloud.com --files a.eml b.eml
  python3 imap_diagnostic.py /path/to/mail_export you@icloud.com --analyze-only
        """,
    )
    parser.add_argument("source_dir", help="Source directory containing .eml files and state file")
    parser.add_argument("email", help="iCloud / Apple ID email address")
    parser.add_argument("password", nargs="?", default=None, help="App-specific password")
    parser.add_argument(
        "--files",
        nargs="+",
        metavar="FILE",
        help="Specific .eml files to diagnose (default: auto-discover from state file)",
    )
    parser.add_argument(
        "--mailbox",
        default=DIAG_MAILBOX,
        help=f"IMAP mailbox for test uploads (default: {DIAG_MAILBOX})",
    )
    parser.add_argument(
        "--analyze-only",
        action="store_true",
        help="Only analyze headers locally, don't connect to IMAP",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=20,
        help="Maximum number of files to diagnose (default: 20)",
    )

    args = parser.parse_args()

    if not os.path.isdir(args.source_dir):
        print(f"Error: Source directory does not exist: {args.source_dir}", file=sys.stderr)
        sys.exit(1)

    # Discover files to diagnose
    if args.files:
        files = [f if os.path.isabs(f) else os.path.join(args.source_dir, f) for f in args.files]
        missing = [f for f in files if not os.path.isfile(f)]
        if missing:
            for m in missing:
                print(f"Warning: File not found: {m}", file=sys.stderr)
            files = [f for f in files if os.path.isfile(f)]
    else:
        files = load_failed_files(args.source_dir)

    if not files:
        print("No failed files found. Provide --files or ensure .imap_upload_state.json exists.")
        sys.exit(1)

    if len(files) > args.max_files:
        print(f"Found {len(files)} failed files, diagnosing first {args.max_files}.")
        files = files[: args.max_files]
    else:
        print(f"Found {len(files)} file(s) to diagnose.")

    # Header analysis (always runs)
    print()
    print("=" * 60)
    print("  HEADER ANALYSIS")
    print("=" * 60)

    for filepath in files:
        try:
            with open(filepath, "rb") as f:
                raw = f.read()
        except OSError as e:
            print(f"\n  {os.path.basename(filepath)}: Cannot read — {e}")
            continue

        issues = analyze_headers(raw)
        basename = os.path.basename(filepath)
        # Truncate long filenames for readability
        display = basename if len(basename) <= 40 else basename[:37] + "..."
        print(f"\n  {display} ({len(raw):,} bytes)")

        if issues["empty_headers"]:
            print(f"    Empty headers: {', '.join(issues['empty_headers'])}")
        if issues["non_ascii"]:
            print("    Non-ASCII bytes in headers: YES")
        if issues["bad_date"]:
            print("    Unparseable Date header: YES")
        if issues["defects"]:
            for d in issues["defects"]:
                print(f"    MIME defect: {d}")
        if not any([issues["empty_headers"], issues["non_ascii"], issues["bad_date"],
                     issues["defects"]]):
            print("    No obvious header issues detected")

    if args.analyze_only:
        print()
        print("Analysis complete (--analyze-only). No IMAP connection made.")
        sys.exit(0)

    # IMAP diagnostic tests
    password = args.password
    if not password:
        password = getpass.getpass(prompt=f"App-specific password for {args.email}: ")

    print()
    print(f"Connecting to {IMAP_HOST}:{IMAP_PORT} ...")
    try:
        conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        conn.login(args.email, password)
    except Exception as e:
        print(f"Error: Could not connect/authenticate: {e}", file=sys.stderr)
        sys.exit(1)

    print("Authenticated. Creating diagnostic mailbox...")

    # Ensure diagnostic mailbox exists
    status, _ = conn.select(args.mailbox)
    if status != "OK":
        conn.create(args.mailbox)
        conn.subscribe(args.mailbox)

    print()
    print("=" * 60)
    print("  IMAP DIAGNOSTIC TESTS")
    print("=" * 60)

    all_results = []
    for i, filepath in enumerate(files, 1):
        basename = os.path.basename(filepath)
        display = basename if len(basename) <= 40 else basename[:37] + "..."
        print(f"\n  [{i}/{len(files)}] {display}")

        result = diagnose_file(conn, filepath, args.mailbox)
        all_results.append(result)

        for test_name, test_result in result["tests"].items():
            status_str = "PASS" if test_result["pass"] else "FAIL"
            print(f"    {test_name:<30} {status_str}")
            if not test_result["pass"] and test_result["detail"] != "OK":
                detail = test_result["detail"]
                if len(detail) > 80:
                    detail = detail[:77] + "..."
                print(f"      {detail}")

    try:
        conn.logout()
    except Exception:
        pass

    # Summary
    print()
    print("=" * 60)
    print("  SUMMARY")
    print("=" * 60)

    for result in all_results:
        if "error" in result:
            print(f"\n  {result['file']}: {result['error']}")
            continue

        passes = [name for name, r in result["tests"].items() if r["pass"]]
        fails = [name for name, r in result["tests"].items() if not r["pass"]]

        print(f"\n  {result['file']}")
        if passes:
            print(f"    Passed: {', '.join(passes)}")
        if fails:
            print(f"    Failed: {', '.join(fails)}")

        # Suggest root cause
        if not passes:
            print("    Diagnosis: All tests failed — likely network/size issue or server rejection")
        elif all("no date" in p for p in passes):
            print("    Diagnosis: Date header causes rejection — date=None workaround should help")
        elif all("sanitized" in p or "minimal" in p for p in passes):
            print("    Diagnosis: Problematic headers — sanitization needed")
        elif all("minimal" in p for p in passes):
            print("    Diagnosis: Multiple header issues — aggressive rewriting needed")
        elif all("Seen" in p for p in passes):
            print("    Diagnosis: Missing \\Seen flag for system folder")

    print()
    print(f"Note: Test messages were uploaded to the '{args.mailbox}' mailbox.")
    print("Remember to delete this mailbox when done.")


if __name__ == "__main__":
    main()

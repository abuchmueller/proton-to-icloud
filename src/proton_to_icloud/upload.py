"""IMAP upload logic: connect to iCloud and APPEND .eml files."""

import email.utils
import getpass
import imaplib
import json
import os
import sys
import time
from argparse import Namespace

from proton_to_icloud.metadata import build_routing_plan, print_routing_summary
from proton_to_icloud.progress import format_duration, print_progress

# ── iCloud IMAP settings ─────────────────────────────────────────────────────
IMAP_HOST = "imap.mail.me.com"
IMAP_PORT = 993

# Throttling: sleep between individual uploads and between batches
SLEEP_PER_MESSAGE = 0.05  # 50ms between messages
SLEEP_PER_BATCH = 2.0  # 2s pause every BATCH_LOG_INTERVAL messages
BATCH_LOG_INTERVAL = 100  # log progress & save state every N messages

# Realistic estimate from testing (seconds per message)
EST_SECONDS_PER_MSG = 1.75

# State file for automatic resume
STATE_FILENAME = ".imap_upload_state.json"


# ── File collection ──────────────────────────────────────────────────────────


def collect_eml_files(source_dir: str, exclude_dir: str | None = None) -> list[str]:
    """Recursively find all .eml files under *source_dir*, sorted for determinism.

    If *exclude_dir* is given, any paths under it are skipped (used to avoid
    re-batching files already moved into output folders).
    """
    exclude_abs = os.path.abspath(exclude_dir) if exclude_dir else None
    eml_files: list[str] = []
    for root, _dirs, files in os.walk(source_dir):
        if exclude_abs and os.path.abspath(root).startswith(exclude_abs):
            continue
        for f in files:
            if f.lower().endswith(".eml"):
                eml_files.append(os.path.join(root, f))
    eml_files.sort()
    return eml_files


# ── Date parsing ─────────────────────────────────────────────────────────────


def parse_date_from_eml(raw_bytes: bytes) -> str | None:
    """Extract the Date header from raw EML bytes for IMAP internaldate."""
    try:
        text = raw_bytes.decode("utf-8", errors="replace")
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


# ── Mailbox management ───────────────────────────────────────────────────────


def ensure_mailbox_exists(conn: imaplib.IMAP4_SSL, mailbox_name: str) -> bool:
    """Create the IMAP mailbox if it doesn't already exist."""
    status, _ = conn.select(mailbox_name)
    if status == "OK":
        conn.close()
        return True

    print(f"  Mailbox '{mailbox_name}' not found. Creating it...")
    status, response = conn.create(mailbox_name)
    if status == "OK":
        print(f"  Created mailbox: {mailbox_name}")
        conn.subscribe(mailbox_name)
        return True

    print(f"  ERROR: Could not create mailbox '{mailbox_name}': {response}")
    return False


# ── State file for automatic resume ──────────────────────────────────────────


def _state_file_path(source_dir: str) -> str:
    return os.path.join(source_dir, STATE_FILENAME)


def save_state(
    source_dir: str,
    index: int,
    uploaded: int,
    failed: int,
    failed_files: list[str],
    mailbox: str,
    routing_mode: str = "single",
) -> None:
    """Write current progress to a JSON state file."""
    data = {
        "last_completed_index": index,
        "uploaded": uploaded,
        "failed": failed,
        "failed_files": failed_files,
        "mailbox": mailbox,
        "routing_mode": routing_mode,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "resume_command_hint": f"--resume-from {index + 1}",
    }
    try:
        with open(_state_file_path(source_dir), "w") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass  # non-critical


def load_state(source_dir: str) -> dict | None:
    """Load previous state if it exists."""
    path = _state_file_path(source_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def clear_state(source_dir: str) -> None:
    """Remove the state file after a successful complete run."""
    try:
        os.remove(_state_file_path(source_dir))
    except OSError:
        pass


def _prepare_retry_files(
    source_dir: str, *, direct: bool, base_mailbox: str
) -> tuple[list[str], dict[str, list[str]], str]:
    """Load failed files from the state file and prepare them for re-upload.

    Returns ``(eml_files, routing, routing_mode)``.
    Exits the process when state is missing, empty, or the routing mode mismatches.
    """
    state = load_state(source_dir)
    if state is None:
        print(
            f"Error: No state file found in {source_dir}. "
            "Run a normal upload first before using --retry-failed.",
            file=sys.stderr,
        )
        sys.exit(1)

    failed_files: list[str] = state.get("failed_files", [])
    if not failed_files:
        print("No failed files recorded in the previous run — nothing to retry.")
        sys.exit(0)

    # Determine expected routing mode from current flags
    current_mode = "direct" if direct else "single"
    saved_mode = state.get("routing_mode", "single")
    # Normalise: both "routed" and "single" are non-direct modes
    saved_is_direct = saved_mode == "direct"
    current_is_direct = current_mode == "direct"
    if saved_is_direct != current_is_direct:
        print(
            f"Error: Previous run used routing_mode='{saved_mode}', "
            f"but current flags resolve to '{'direct' if current_is_direct else 'non-direct'}'.\n"
            f"Use the same --direct flag as the original upload.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Filter out files that no longer exist on disk
    existing: list[str] = []
    for path in failed_files:
        if os.path.isfile(path):
            existing.append(path)
        else:
            print(f"  WARNING: Skipping missing file: {path}")

    if not existing:
        print("All previously failed files have been removed from disk — nothing to retry.")
        sys.exit(0)

    routing_mode = "direct" if direct else "single"
    routing = build_routing_plan(existing, source_dir, direct=direct, base_mailbox=base_mailbox)

    # Re-derive routing_mode to match what run_upload expects
    if not direct and len(routing) > 1:
        routing_mode = "routed"

    return existing, routing, routing_mode


# ── EML sanitisation ─────────────────────────────────────────────────────


def sanitize_eml_headers(raw: bytes) -> bytes:
    """Remove headers with empty values that iCloud IMAP rejects.

    Some exports contain headers like ``X-Mozilla-Keys: \\r\\n`` (name, colon,
    whitespace-only value).  iCloud's IMAP server returns ``BAD Parse Error``
    for these.  This strips them from the header section only, leaving the
    message body untouched.
    """
    # Locate the header/body boundary (first blank line)
    for sep in (b"\r\n\r\n", b"\n\n"):
        pos = raw.find(sep)
        if pos >= 0:
            break
    else:
        return raw  # no boundary found — leave unchanged

    header_section = raw[:pos]
    rest = raw[pos:]  # separator + body, returned verbatim

    line_end = b"\r\n" if b"\r\n" in header_section else b"\n"
    lines = header_section.split(line_end)

    cleaned: list[bytes] = []
    for line in lines:
        # A header line starts with a non-whitespace token followed by ':'
        if b":" in line and not line.startswith((b" ", b"\t")):
            _, _, value = line.partition(b":")
            if value.strip() == b"":
                continue  # drop empty-value header
        cleaned.append(line)

    return line_end.join(cleaned) + rest


def _strip_non_ascii_headers(raw: bytes) -> bytes:
    """Replace non-ASCII bytes in the header section with '?'.

    iCloud's IMAP server rejects messages whose headers contain raw 8-bit
    bytes (RFC 5322 requires 7-bit headers).  Proton exports of Gmail
    imports sometimes include unencoded UTF-8 in headers like
    ``X-Gmail-Labels`` or ``X-Attached``.
    """
    for sep in (b"\r\n\r\n", b"\n\n"):
        pos = raw.find(sep)
        if pos >= 0:
            break
    else:
        return raw

    header = raw[:pos]
    if all(b <= 127 for b in header):
        return raw  # fast path — already 7-bit clean

    cleaned = bytes(b if b <= 127 else ord(b"?") for b in header)
    return cleaned + raw[pos:]


# ── Sent / Drafts flag helper ────────────────────────────────────────────────

# iCloud system folders that expect the \Seen flag on APPEND.
_SEEN_MAILBOXES: frozenset[str] = frozenset(
    {
        "Sent Messages",
        "Drafts",
        "Deleted Messages",
        "Junk",
    }
)


def _flags_for_mailbox(mailbox: str) -> str:
    r"""Return IMAP flag string for *mailbox*.

    Messages appended to Sent / Drafts / Trash / Junk are marked ``\Seen``
    because iCloud's strict IMAP parser rejects unread messages in these
    system folders.
    """
    if mailbox in _SEEN_MAILBOXES:
        return r"\Seen"
    return ""


def _is_unavailable(response: list) -> bool:
    """Return True if the IMAP response contains ``[UNAVAILABLE]``."""
    for item in response:
        if isinstance(item, bytes) and b"[UNAVAILABLE]" in item:
            return True
        if isinstance(item, str) and "[UNAVAILABLE]" in item:
            return True
    return False


def _quote_mailbox(name: str) -> str:
    """Quote an IMAP mailbox name if it contains spaces.

    Python's ``imaplib`` does **not** quote mailbox arguments.  Names with
    spaces (e.g. ``Sent Messages``) are sent verbatim, which causes the
    server to misparse the APPEND command → ``BAD Parse Error``.
    """
    if " " in name:
        return f'"{name}"'
    return name


# ── Core upload loop ─────────────────────────────────────────────────────────


def upload_eml_files(
    conn: imaplib.IMAP4_SSL,
    eml_files: list[str],
    mailbox_name: str,
    source_dir: str,
    resume_from: int = 0,
    routing: dict[str, list[str]] | None = None,
    routing_mode: str = "single",
    reconnect=None,
) -> tuple[int, int, int, list[str]]:
    """Upload .eml file paths to the IMAP mailbox via APPEND.

    When *routing* is provided, each file is uploaded to its resolved target
    folder instead of the single *mailbox_name*.

    *reconnect*, when provided, is a zero-argument callable that returns a
    fresh ``IMAP4_SSL`` connection.  It is invoked automatically when the
    connection drops mid-upload.

    Returns (uploaded, skipped, failed, failed_files).
    """
    # Build reverse lookup: filepath → target mailbox
    file_to_mailbox: dict[str, str] = {}
    if routing:
        for mbox, paths in routing.items():
            for path in paths:
                file_to_mailbox[path] = mbox

    total = len(eml_files)
    remaining = total - resume_from
    uploaded = 0
    skipped = 0
    failed = 0
    failed_files: list[str] = []
    processed = 0
    start_time = time.time()

    for i, filepath in enumerate(eml_files):
        if i < resume_from:
            skipped += 1
            continue

        target = file_to_mailbox.get(filepath, mailbox_name) if file_to_mailbox else mailbox_name

        # Read raw EML bytes
        try:
            with open(filepath, "rb") as f:
                raw_message = f.read()
        except OSError as e:
            sys.stdout.write("\n")
            print(f"  WARNING: Cannot read {filepath}: {e}")
            failed += 1
            failed_files.append(filepath)
            processed += 1
            print_progress(processed, remaining, uploaded, failed, start_time)
            continue

        # Sanitise headers that iCloud's strict parser rejects
        raw_message = sanitize_eml_headers(raw_message)
        raw_message = _strip_non_ascii_headers(raw_message)

        # Extract original Date for IMAP internal date
        internal_date = parse_date_from_eml(raw_message)

        # IMAP APPEND
        try:
            flags = _flags_for_mailbox(target)
            status, response = conn.append(
                _quote_mailbox(target), flags, internal_date, raw_message
            )

            # iCloud's IMAP server sometimes returns [UNAVAILABLE] when the
            # internal date triggers a server-side bug.  Retry once without
            # the date so iCloud uses the current time instead.
            if status != "OK" and internal_date is not None and _is_unavailable(response):
                status, response = conn.append(
                    _quote_mailbox(target), flags, None, raw_message
                )

            if status == "OK":
                uploaded += 1
            else:
                sys.stdout.write("\n")
                print(f"  WARNING: APPEND failed for {os.path.basename(filepath)}: {response}")
                failed += 1
                failed_files.append(filepath)

        except (imaplib.IMAP4.error, imaplib.IMAP4.abort, OSError) as e:
            sys.stdout.write("\n")
            print(f"  WARNING: IMAP error for {os.path.basename(filepath)}: {e}")
            failed += 1
            failed_files.append(filepath)

            # Check whether the connection is still alive; reconnect if needed
            if reconnect:
                try:
                    conn.noop()
                except Exception:
                    try:
                        print("  Connection lost. Reconnecting...")
                        conn = reconnect()
                        print("  Reconnected successfully.")
                    except Exception:
                        print("  ERROR: Could not reconnect.")
                        reconnect = None  # stop retrying on every subsequent file

        processed += 1
        print_progress(processed, remaining, uploaded, failed, start_time)

        # Save state periodically for resume
        if processed % BATCH_LOG_INTERVAL == 0:
            save_state(source_dir, i, uploaded, failed, failed_files, mailbox_name, routing_mode)
            time.sleep(SLEEP_PER_BATCH)
        else:
            time.sleep(SLEEP_PER_MESSAGE)

    # Final progress bar at 100%
    print_progress(processed, remaining, uploaded, failed, start_time)
    sys.stdout.write("\n")

    save_state(source_dir, total - 1, uploaded, failed, failed_files, mailbox_name, routing_mode)

    return uploaded, skipped, failed, failed_files


# ── CLI helpers ──────────────────────────────────────────────────────────────


def _prompt_auto_resume(
    source: str, total: int, resume_from: int, routing_mode: str = "single"
) -> int:
    """Check for a saved state file and prompt the user to resume."""
    if resume_from != 0:
        return resume_from

    prev_state = load_state(source)
    if not prev_state or prev_state.get("last_completed_index", -1) < 0:
        return 0

    suggested = prev_state["last_completed_index"] + 1
    if suggested >= total:
        return 0

    # Warn if routing mode changed since the previous run
    saved_mode = prev_state.get("routing_mode", "single")
    if saved_mode != routing_mode:
        print()
        print(
            f"  WARNING: Previous run used routing_mode='{saved_mode}', "
            f"but current flags resolve to '{routing_mode}'."
        )
        print(f"  Delete {_state_file_path(source)} and restart to avoid mixed routing.")
        print()
        sys.exit(1)

    print()
    print(
        f"  Found previous state: {prev_state['uploaded']} uploaded, "
        f"{prev_state['failed']} failed "
        f"(as of {prev_state.get('timestamp', '?')})"
    )
    print(f"  To resume, re-run with: --resume-from {suggested}")
    print(f"  To start fresh, delete {_state_file_path(source)}")
    print()
    answer = input(f"  Resume from file #{suggested + 1}? [Y/n] ").strip().lower()
    if answer in ("", "y", "yes"):
        print(f"  Resuming from file #{suggested + 1}.")
        return suggested

    print("  Starting from the beginning.")
    return 0


def _connect_imap(email: str, password: str) -> imaplib.IMAP4_SSL:
    """Connect and authenticate to iCloud IMAP. Exits on failure."""
    print(f"Connecting to {IMAP_HOST}:{IMAP_PORT} ...")
    try:
        conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    except Exception as e:
        print(f"Error: Could not connect to {IMAP_HOST}:{IMAP_PORT}: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Authenticating as {email} ...")
    try:
        conn.login(email, password)
    except imaplib.IMAP4.error as e:
        print(f"Error: Authentication failed: {e}", file=sys.stderr)
        print("Make sure you are using an App-Specific Password from https://appleid.apple.com")
        sys.exit(1)

    print("Authenticated successfully.")
    print()
    return conn


def _print_summary(
    total: int,
    uploaded: int,
    skipped: int,
    failed: int,
    failed_files: list[str],
    mailbox: str,
    elapsed: float,
    source: str,
    routing: dict[str, list[str]] | None = None,
) -> None:
    """Print the final upload summary and handle failure log."""
    print()
    print("=" * 60)
    print("  UPLOAD COMPLETE")
    print("=" * 60)
    print(f"  Total .eml files found:  {total}")
    print(f"  Skipped (--resume-from): {skipped}")
    print(f"  Uploaded successfully:   {uploaded}")
    print(f"  Failed:                  {failed}")
    if routing and len(routing) > 1:
        print("  Target mailboxes:")
        for folder in sorted(routing, key=lambda f: (-len(routing[f]), f)):
            print(f"    {folder:<24} {len(routing[folder]):>7,} emails")
    else:
        print(f"  Target mailbox:          {mailbox}")
    print(f"  Elapsed time:            {format_duration(elapsed)}")
    if uploaded > 0:
        rate = elapsed / uploaded
        print(f"  Avg per message:         {rate:.2f}s")
        print(f"  Throughput:              {3600 / rate:.0f} messages/hour")
    print("=" * 60)

    if failed_files:
        fail_log = os.path.join(os.path.dirname(os.path.abspath(source)), "failed_uploads.txt")
        with open(fail_log, "w") as f:
            for path in failed_files:
                f.write(path + "\n")
        print(f"\nFailed file paths written to: {fail_log}")
        print("To retry just the failed files: --retry-failed")


# ── CLI orchestrator ─────────────────────────────────────────────────────────


def _list_existing_mailboxes(conn: imaplib.IMAP4_SSL) -> set[str]:
    """Return the set of mailbox names already present on the server."""
    status, data = conn.list()
    existing: set[str] = set()
    if status != "OK" or data is None:
        return existing
    for item in data:
        if item is None:
            continue
        # Each item is like: b'(\\HasNoChildren) "/" "INBOX"'
        line = item.decode("utf-8", errors="replace") if isinstance(item, bytes) else str(item)
        # Mailbox name is after the last space-separated quoted or unquoted token
        parts = line.rsplit('" ', 1)
        if len(parts) == 2:
            name = parts[1].strip().strip('"')
            existing.add(name)
    return existing


def _ensure_all_mailboxes(conn: imaplib.IMAP4_SSL, routing: dict[str, list[str]]) -> None:
    """Create every target folder from the routing plan, exiting on failure."""
    existing = _list_existing_mailboxes(conn)
    for folder in sorted(routing):
        if folder in existing:
            continue
        if not ensure_mailbox_exists(conn, folder):
            conn.logout()
            sys.exit(1)


def _run_upload_loop(
    conn: imaplib.IMAP4_SSL,
    eml_files: list[str],
    mailbox: str,
    source: str,
    resume_from: int,
    routing: dict[str, list[str]],
    routing_mode: str = "single",
    reconnect=None,
) -> tuple[int, int, int, list[str], float]:
    """Execute the upload loop, handling Ctrl-C gracefully.

    Returns (uploaded, skipped, failed, failed_files, elapsed).
    """
    remaining = len(eml_files) - resume_from
    print(f"Starting upload of {remaining:,} files ...")
    print()

    start_time = time.time()
    try:
        uploaded, skipped, failed, failed_files = upload_eml_files(
            conn,
            eml_files,
            mailbox,
            source,
            resume_from=resume_from,
            routing=routing,
            routing_mode=routing_mode,
            reconnect=reconnect,
        )
    except KeyboardInterrupt:
        elapsed = time.time() - start_time
        sys.stdout.write("\n")
        print()
        print("Interrupted by user. Logging out...")
        try:
            conn.logout()
        except Exception:
            pass
        prev = load_state(source)
        if prev:
            idx = prev["last_completed_index"] + 1
            print(f"Progress saved. Resume with: --resume-from {idx}")
            print(
                f"  ({prev['uploaded']} uploaded, {prev['failed']} failed, "
                f"{format_duration(elapsed)} elapsed)"
            )
        else:
            print("Re-run the same command to resume.")
        sys.exit(130)

    return uploaded, skipped, failed, failed_files, time.time() - start_time


def run_upload(args: Namespace) -> None:
    """Entry point called from cli.py for the ``upload`` subcommand."""

    source = args.source
    direct = args.direct
    retry_failed = args.retry_failed

    if retry_failed and args.resume_from != 0:
        print(
            "Error: --retry-failed and --resume-from are mutually exclusive.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not os.path.isdir(source):
        print(f"Error: Source directory does not exist: {source}", file=sys.stderr)
        sys.exit(1)

    print(f"Source directory: {source}")
    print(f"Target mailbox:  {args.mailbox}")
    if direct:
        print("Routing mode:    --direct (native iCloud folders)")
    print()

    if retry_failed:
        # ── Retry path ────────────────────────────────────────────────
        eml_files, routing, routing_mode = _prepare_retry_files(
            source, direct=direct, base_mailbox=args.mailbox
        )
        total = len(eml_files)
        resume_from = 0
        print(f"Retrying {total:,} failed files from previous run...")
        print_routing_summary(routing)
    else:
        # ── Normal path ───────────────────────────────────────────────
        eml_files = collect_eml_files(source)
        total = len(eml_files)

        if total == 0:
            print(f"No .eml files found under {source}")
            sys.exit(1)

        print(f"Found {total:,} .eml files.")

        # ── Build routing plan ────────────────────────────────────────
        print(f"Reading metadata for {total:,} emails...")
        routing = build_routing_plan(eml_files, source, direct=direct, base_mailbox=args.mailbox)
        print_routing_summary(routing)

        # Determine routing mode for state file
        routing_mode = "direct" if direct else ("routed" if len(routing) > 1 else "single")

        resume_from = _prompt_auto_resume(
            source, total, args.resume_from, routing_mode=routing_mode
        )

        if resume_from > 0:
            print(f"Skipping first {resume_from} files.")

    remaining = total - resume_from
    est_seconds = remaining * EST_SECONDS_PER_MSG
    print(f"Estimated time: ~{format_duration(est_seconds)} for {remaining:,} files.")
    print()

    # ── Dry run ───────────────────────────────────────────────────────
    if args.dry_run:
        print("DRY RUN — no connection made, no files uploaded.")
        for folder in sorted(routing, key=lambda f: (-len(routing[f]), f)):
            count = len(routing[folder])
            print(f"  Would upload {count:,} files to '{folder}'.")
        sys.exit(0)

    # ── Get password ──────────────────────────────────────────────────
    password = args.password
    if not password:
        password = getpass.getpass(prompt=f"App-specific password for {args.email}: ")

    conn = _connect_imap(args.email, password)

    def reconnect():
        """Return a fresh authenticated IMAP connection."""
        c = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        c.login(args.email, password)
        return c

    # ── Ensure target mailboxes exist ─────────────────────────────────
    if not args.no_create_mailbox:
        _ensure_all_mailboxes(conn, routing)

    # ── Upload ────────────────────────────────────────────────────────
    uploaded, skipped, failed, failed_files, elapsed = _run_upload_loop(
        conn, eml_files, args.mailbox, source, resume_from, routing, routing_mode, reconnect
    )

    try:
        conn.logout()
    except Exception:
        pass

    _print_summary(
        total,
        uploaded,
        skipped,
        failed,
        failed_files,
        args.mailbox,
        elapsed,
        source,
        routing=routing,
    )

    if failed == 0:
        clear_state(source)
    else:
        print("\nState saved. Re-run the same command to resume from where it left off.")
        sys.exit(1)

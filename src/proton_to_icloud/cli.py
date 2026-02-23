"""CLI entry point: argparse with ``upload`` and ``batch`` subcommands."""

import argparse
import sys

from proton_to_icloud import __version__


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="proton-to-icloud",
        description="Migrate Proton Mail exports (.eml) to iCloud Mail via IMAP.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command")

    # ── upload ────────────────────────────────────────────────────────
    upload_p = sub.add_parser(
        "upload",
        help="Upload .eml files to iCloud Mail via IMAP APPEND.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  proton-to-icloud upload -s ./eml -m "Proton-Import" -e you@icloud.com
  proton-to-icloud upload -s ./eml -m "Proton-Import/Sent" -e you@icloud.com --dry-run
        """,
    )
    upload_p.add_argument(
        "-s",
        "--source",
        required=True,
        help="Directory containing .eml files (searched recursively).",
    )
    upload_p.add_argument(
        "-m",
        "--mailbox",
        default="Proton-Import",
        help='Target IMAP folder. Default: "Proton-Import". '
        'Use "/" for hierarchy, e.g. "Proton-Import/Sent".',
    )
    upload_p.add_argument(
        "-e",
        "--email",
        required=True,
        help="Your iCloud / Apple ID email address.",
    )
    upload_p.add_argument(
        "-p",
        "--password",
        default=None,
        help="App-specific password. Prompted securely if omitted.",
    )
    upload_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and count files without connecting or uploading.",
    )
    upload_p.add_argument(
        "--resume-from",
        type=int,
        default=0,
        metavar="N",
        help="Skip the first N files (for manual resume). Default: 0.",
    )
    upload_p.add_argument(
        "--no-create-mailbox",
        action="store_true",
        help="Do not auto-create the target mailbox if it is missing.",
    )

    # ── batch ─────────────────────────────────────────────────────────
    batch_p = sub.add_parser(
        "batch",
        help="Split .eml files into numbered batch folders for Apple Mail drag-and-drop.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  proton-to-icloud batch -s ./eml -n 1000
  proton-to-icloud batch -s ./eml -n 500 -o ./batches --move
        """,
    )
    batch_p.add_argument(
        "-s",
        "--source",
        required=True,
        help="Directory containing .eml files (searched recursively).",
    )
    batch_p.add_argument(
        "-n",
        "--batch-size",
        type=int,
        default=1000,
        help="Number of .eml files per batch folder. Default: 1000.",
    )
    batch_p.add_argument(
        "-o",
        "--output",
        default=None,
        help="Where to create batch_NNN folders. Default: <source>/batches.",
    )
    batch_p.add_argument(
        "--move",
        action="store_true",
        help="Move files instead of copying (default: copy).",
    )

    return parser


def main() -> None:
    """Parse arguments and dispatch to the appropriate subcommand."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    # Lazy imports keep startup fast for --version / --help
    if args.command == "upload":
        from proton_to_icloud.upload import run_upload

        run_upload(args)
    elif args.command == "batch":
        from proton_to_icloud.batch import run_batch

        run_batch(args)
    else:
        parser.print_help()
        sys.exit(1)

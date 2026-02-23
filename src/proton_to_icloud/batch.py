"""Batch-split .eml files into numbered subdirectories.

Useful for manually dragging batches into Apple Mail when IMAP upload
is not an option.  Default behaviour is to **copy** files (not move)
so the original export stays intact.
"""

import os
import re
import shutil
import sys
from argparse import Namespace

from proton_to_icloud.upload import collect_eml_files


def detect_highest_batch_index(output_dir: str) -> int:
    """Scan *output_dir* for existing ``batch_NNN`` folders and return the highest N.

    Returns 0 if no batch folders exist.
    """
    pattern = re.compile(r"^batch_(\d+)$")
    highest = 0
    if not os.path.isdir(output_dir):
        return 0
    for name in os.listdir(output_dir):
        m = pattern.match(name)
        if m and os.path.isdir(os.path.join(output_dir, name)):
            idx = int(m.group(1))
            if idx > highest:
                highest = idx
    return highest


def run_batch(args: Namespace) -> None:
    """Entry point called from cli.py for the ``batch`` subcommand."""

    source = args.source
    batch_size: int = args.batch_size
    output_dir: str = args.output or os.path.join(source, "batches")
    move_files: bool = args.move

    # ── Validate ──────────────────────────────────────────────────────
    if not os.path.isdir(source):
        print(f"Error: Source directory does not exist: {source}", file=sys.stderr)
        sys.exit(1)

    if batch_size < 1:
        print(f"Error: Batch size must be a positive integer, got: {batch_size}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)
    output_dir_abs = os.path.abspath(output_dir)

    print(f"Source directory: {source}")
    print(f"Output directory: {output_dir}")
    print(f"Batch size:       {batch_size}")
    print(f"Mode:             {'move' if move_files else 'copy'}")
    print()

    # ── Collect .eml files ────────────────────────────────────────────
    eml_files = collect_eml_files(source, exclude_dir=output_dir_abs)
    total = len(eml_files)

    if total == 0:
        print(f"No .eml files found under {source}")
        sys.exit(1)

    print(f"Found {total} .eml files.")

    # Count non-eml files for the summary
    non_eml = 0
    for root, _dirs, files in os.walk(source):
        if os.path.abspath(root).startswith(output_dir_abs):
            continue
        for f in files:
            if not f.lower().endswith(".eml"):
                non_eml += 1

    # ── Detect existing batch folders ─────────────────────────────────
    highest = detect_highest_batch_index(output_dir)
    if highest > 0:
        print(f"Detected existing batches up to batch_{highest:03d}; continuing from next index.")
        print()

    batch_index = highest + 1

    # ── Batch and copy/move ───────────────────────────────────────────
    file_counter = 0
    files_in_current_batch = 0
    batches_created = 0
    failures = 0
    current_batch_dir = ""
    op = shutil.move if move_files else shutil.copy2

    for filepath in eml_files:
        # Start a new batch when needed
        if files_in_current_batch == 0:
            current_batch_dir = os.path.join(output_dir, f"batch_{batch_index:03d}")
            os.makedirs(current_batch_dir, exist_ok=True)
            print(f"Creating batch directory: batch_{batch_index:03d}")
            batches_created += 1

        try:
            op(filepath, current_batch_dir)
            file_counter += 1
            files_in_current_batch += 1
        except OSError as e:
            print(f"  Warning: failed to {'move' if move_files else 'copy'} {filepath}: {e}")
            failures += 1

        # Log progress every 1000 files
        if file_counter > 0 and file_counter % 1000 == 0:
            print(f"  … {file_counter} files processed so far")

        # Roll over to next batch
        if files_in_current_batch >= batch_size:
            files_in_current_batch = 0
            batch_index += 1

    # ── Summary ───────────────────────────────────────────────────────
    print()
    print("Done.")
    print()
    print("Summary:")
    print(f"  .eml files processed:    {file_counter}")
    print(f"  Batch size:              {batch_size}")
    print(f"  Batch folders created:   {batches_created}")
    print(f"  Non-.eml files skipped:  {non_eml}")
    if failures > 0:
        print(f"  Failures:                {failures}")

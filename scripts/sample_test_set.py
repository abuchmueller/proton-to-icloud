#!/usr/bin/env python3
"""Sample .eml files from a Proton export and copy them (+ metadata) to a test dir.

Usage:
    python scripts/sample_test_set.py SOURCE_DIR [-n COUNT] [-o OUTPUT_DIR]

With -n (default 7), guarantees at least one email per routable folder label
(Inbox, Sent, Drafts, Spam, Trash, Archive, Fallback) and fills the remaining
quota by round-robin across buckets.  The output directory also gets a copy of
labels.json so the upload tool can route correctly.
"""

import argparse
import json
import os
import random
import shutil
import sys

# Same constants as metadata.py — duplicated here so the script is standalone.
SKIP_LABEL_IDS = frozenset({"1", "2", "5", "9", "10", "12", "15", "16"})
LABEL_PRIORITY = ["0", "7", "8", "4", "3", "6"]
LABEL_NAMES = {
    "0": "Inbox",
    "7": "Sent",
    "8": "Drafts",
    "4": "Spam",
    "3": "Trash",
    "6": "Archive",
}
ALL_BUCKETS = LABEL_PRIORITY + [None]


def resolve_label(label_ids: list[str]) -> str | None:
    """Return the highest-priority routable label ID, or None."""
    real = [lid for lid in label_ids if lid not in SKIP_LABEL_IDS]
    for priority_id in LABEL_PRIORITY:
        if priority_id in real:
            return priority_id
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Sample emails from a Proton export.")
    parser.add_argument("source", help="Directory containing .eml + .metadata.json files.")
    parser.add_argument("-n", "--count", type=int, default=7, help="Total emails to sample.")
    parser.add_argument("-o", "--output", default=None, help="Output directory.")
    args = parser.parse_args()

    source = args.source
    count = args.count
    output = args.output or os.path.join(source, "..", "test_sample")
    output = os.path.abspath(output)

    if not os.path.isdir(source):
        print(f"Error: {source} is not a directory", file=sys.stderr)
        sys.exit(1)

    # Collect all emails into buckets by label
    buckets: dict[str | None, list[str]] = {b: [] for b in ALL_BUCKETS}

    print(f"Scanning {source} ...")
    for fname in os.listdir(source):
        if not fname.endswith(".metadata.json"):
            continue

        meta_path = os.path.join(source, fname)
        try:
            with open(meta_path) as f:
                data = json.load(f)
            label_ids = data["Payload"]["LabelIDs"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            continue

        base = fname.removesuffix(".metadata.json")
        eml_path = os.path.join(source, base + ".eml")
        if not os.path.isfile(eml_path):
            continue

        bucket = resolve_label(label_ids)
        buckets[bucket].append(base)

    # Shuffle each bucket for random sampling
    for bases in buckets.values():
        random.shuffle(bases)

    # Phase 1: guarantee one per non-empty bucket
    selected: list[str] = []
    remaining_by_bucket: dict[str | None, list[str]] = {}
    for bucket_id in ALL_BUCKETS:
        bases = buckets[bucket_id]
        if bases:
            selected.append(bases[0])
            remaining_by_bucket[bucket_id] = bases[1:]
        else:
            remaining_by_bucket[bucket_id] = []

    # Phase 2: fill up to count by round-robin across buckets
    while len(selected) < count:
        added = False
        for bucket_id in ALL_BUCKETS:
            if len(selected) >= count:
                break
            leftovers = remaining_by_bucket[bucket_id]
            if leftovers:
                selected.append(leftovers.pop(0))
                added = True
        if not added:
            break  # exhausted all buckets

    # Report
    print()
    for bucket_id in ALL_BUCKETS:
        name = LABEL_NAMES.get(bucket_id, "Fallback") if bucket_id else "Fallback"
        total_avail = len(buckets[bucket_id])
        picked = sum(1 for b in selected if b in buckets[bucket_id])
        print(f"  {name:<10} {picked:>4} sampled  ({total_avail:,} available)")

    if not selected:
        print("\nNo matching emails found.", file=sys.stderr)
        sys.exit(1)

    # Copy files to output dir
    if os.path.isdir(output):
        shutil.rmtree(output)
    os.makedirs(output)

    for base in selected:
        for ext in (".eml", ".metadata.json"):
            src = os.path.join(source, base + ext)
            dst = os.path.join(output, base + ext)
            shutil.copy2(src, dst)

    # Copy labels.json
    for candidate in [
        os.path.join(source, "labels.json"),
        os.path.join(source, "json", "labels.json"),
        os.path.join(os.path.dirname(os.path.abspath(source)), "labels.json"),
    ]:
        if os.path.isfile(candidate):
            shutil.copy2(candidate, os.path.join(output, "labels.json"))
            break

    print(f"\nCopied {len(selected)} emails ({len(selected) * 2} files) + labels.json to:")
    print(f"  {output}")
    print()
    print("Next steps:")
    print(f"  1. Dry run:  proton-to-icloud upload -s '{output}' -e YOU@ICLOUD --direct --dry-run")
    print(f"  2. Upload:   proton-to-icloud upload -s '{output}' -e YOU@ICLOUD --direct")


if __name__ == "__main__":
    main()

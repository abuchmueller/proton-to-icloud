"""Terminal progress bar and duration formatting utilities."""

import os
import sys
import time


def format_duration(seconds: float) -> str:
    """Format seconds into human-readable H:MM:SS or M:SS."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h}h {m:02d}m {s:02d}s"


def print_progress(
    current: int,
    total: int,
    uploaded: int,
    failed: int,
    start_time: float,
) -> None:
    """Print a single-line progress bar with ETA and diagnostics."""
    elapsed = time.time() - start_time
    pct = (current / total) * 100 if total else 0

    # ETA based on observed average
    if current > 0:
        avg = elapsed / current
        remaining = (total - current) * avg
        eta_str = format_duration(remaining)
    else:
        eta_str = "?"

    # Progress bar: 30 chars wide
    bar_width = 30
    filled = int(bar_width * current / total) if total else 0
    bar = "█" * filled + "░" * (bar_width - filled)

    line = (
        f"\r  {bar} {pct:5.1f}%  "
        f"{current}/{total}  "
        f"ok:{uploaded} fail:{failed}  "
        f"elapsed:{format_duration(elapsed)} eta:{eta_str}"
    )

    # Truncate to terminal width to avoid wrapping
    try:
        term_width = os.get_terminal_size().columns
        line = line[:term_width]
    except OSError:
        pass

    sys.stdout.write(line)
    sys.stdout.flush()

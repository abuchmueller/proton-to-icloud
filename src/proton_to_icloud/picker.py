"""Interactive terminal directory picker for selecting Proton Mail export folders."""

from __future__ import annotations

import os
import re
from typing import NamedTuple

# ---------------------------------------------------------------------------
# 1. Pure functions (testable without curses)
# ---------------------------------------------------------------------------

_PROTON_EXPORT_RE = re.compile(r"^mail_\d{8}_\d{6}$")


class DirEntry(NamedTuple):
    name: str
    path: str
    eml_count: int  # -1 means permission denied


def detect_proton_exports(base_dir: str) -> list[str]:
    """Scan one level deep for ``*/mail_YYYYMMDD_HHMMSS/`` directories.

    Returns full paths sorted newest-first.
    """
    matches: list[str] = []
    try:
        top_entries = os.scandir(base_dir)
    except (OSError, PermissionError):
        return matches

    for top in top_entries:
        if not top.is_dir(follow_symlinks=False):
            continue
        try:
            for sub in os.scandir(top.path):
                if sub.is_dir(follow_symlinks=False) and _PROTON_EXPORT_RE.match(sub.name):
                    matches.append(sub.path)
        except (OSError, PermissionError):
            continue

    matches.sort(reverse=True)
    return matches


def list_directory_entries(directory: str) -> list[DirEntry]:
    """List subdirectories with direct ``.eml`` file counts.

    ``../`` is always first. Remaining entries are sorted alphabetically.
    ``eml_count`` is set to ``-1`` on ``PermissionError``.
    """
    parent = os.path.dirname(os.path.abspath(directory))
    entries: list[DirEntry] = [DirEntry(name="../", path=parent, eml_count=0)]

    subdirs: list[DirEntry] = []
    try:
        for item in os.scandir(directory):
            if not item.is_dir(follow_symlinks=False):
                continue
            try:
                eml_count = sum(
                    1 for f in os.scandir(item.path) if f.name.endswith(".eml") and f.is_file()
                )
            except PermissionError:
                eml_count = -1
            subdirs.append(DirEntry(name=item.name + "/", path=item.path, eml_count=eml_count))
    except PermissionError:
        pass

    subdirs.sort(key=lambda e: e.name.lower())
    entries.extend(subdirs)
    return entries


def pick_start_directory() -> str:
    """Choose the initial directory for the picker.

    If exactly one Proton export is detected in the cwd, start there;
    otherwise start in the cwd.
    """
    cwd = os.getcwd()
    exports = detect_proton_exports(cwd)
    if len(exports) == 1:
        return exports[0]
    return cwd


# ---------------------------------------------------------------------------
# 2. Curses UI
# ---------------------------------------------------------------------------


def _render(
    stdscr,
    current_dir: str,
    entries: list[DirEntry],
    selected_idx: int,
    scroll_offset: int,
    message: str,
) -> None:
    """Draw the picker screen."""
    stdscr.erase()
    max_y, max_x = stdscr.getmaxyx()

    if max_y < 8 or max_x < 40:
        stdscr.addnstr(0, 0, "Terminal too small — resize or press q", max_x - 1)
        stdscr.refresh()
        return

    import curses

    has_color = curses.has_colors()

    row = 0
    stdscr.addnstr(row, 0, "Pick source directory", max_x - 1, curses.A_BOLD)
    row += 2

    # Show current directory path (truncate from the left if needed)
    dir_display = current_dir
    if len(dir_display) > max_x - 1:
        dir_display = "..." + dir_display[-(max_x - 4) :]
    stdscr.addnstr(row, 0, dir_display, max_x - 1, curses.A_DIM)
    row += 2

    # Available rows for directory listing
    list_height = max_y - row - 3  # leave room for footer
    if list_height < 1:
        list_height = 1

    visible_entries = entries[scroll_offset : scroll_offset + list_height]

    for i, entry in enumerate(visible_entries):
        abs_idx = scroll_offset + i
        attr = curses.A_REVERSE if abs_idx == selected_idx else 0

        # Build the line: "  name                 count .eml"
        name_part = f"  {entry.name}"

        if entry.name == "../":
            count_part = "(go up)"
        elif entry.eml_count < 0:
            count_part = "(permission denied)"
        elif entry.eml_count > 0:
            count_part = f"{entry.eml_count:,} .eml"
        else:
            count_part = ""

        # Pad between name and count
        gap = max_x - len(name_part) - len(count_part) - 2
        if gap < 2:
            gap = 2
        line = name_part + " " * gap + count_part
        line = line[: max_x - 1]

        if abs_idx == selected_idx:
            stdscr.addnstr(row, 0, line, max_x - 1, attr)
        else:
            # Color directory names in cyan, counts in green
            stdscr.addnstr(row, 0, " " * (max_x - 1), max_x - 1)  # clear line
            stdscr.addnstr(row, 0, name_part, max_x - 1)
            if has_color and entry.eml_count > 0:
                count_col = max_x - len(count_part) - 2
                if count_col > len(name_part):
                    stdscr.addnstr(
                        row, count_col, count_part, max_x - count_col - 1, curses.color_pair(2)
                    )
            elif count_part:
                count_col = max_x - len(count_part) - 2
                if count_col > len(name_part):
                    stdscr.addnstr(row, count_col, count_part, max_x - count_col - 1, curses.A_DIM)
        row += 1

    # Footer
    footer_row = max_y - 2
    footer = "[Enter] open  [Space] select here  [q/Esc] cancel"
    stdscr.addnstr(footer_row, 0, footer, max_x - 1, curses.A_DIM)

    if message:
        stdscr.addnstr(footer_row + 1, 0, message, max_x - 1, curses.A_BOLD)

    stdscr.refresh()


def _navigate_to(directory: str) -> tuple[str, list[DirEntry]]:
    """Change to *directory* and return (abs_path, entries)."""
    abs_dir = os.path.abspath(directory)
    return abs_dir, list_directory_entries(abs_dir)


def _handle_key(key, state: dict) -> str | None | bool:
    """Process a single keypress, mutating *state* in place.

    Returns:
      ``str``  — selected directory (done)
      ``None`` — user cancelled (done)
      ``True`` — continue looping
    """
    import curses

    if key in (ord("q"), 27):
        return None

    if key in (curses.KEY_UP, ord("k")):
        if state["idx"] > 0:
            state["idx"] -= 1
            if state["idx"] < state["scroll"]:
                state["scroll"] = state["idx"]
        return True

    if key in (curses.KEY_DOWN, ord("j")):
        if state["idx"] < len(state["entries"]) - 1:
            state["idx"] += 1
            if state["idx"] >= state["scroll"] + state["height"]:
                state["scroll"] = state["idx"] - state["height"] + 1
        return True

    if key in (curses.KEY_ENTER, 10, 13, curses.KEY_RIGHT, ord("l")):
        target = state["entries"][state["idx"]].path
        if not os.access(target, os.R_OK):
            state["msg"] = "Permission denied"
            return True
        state["dir"], state["entries"] = _navigate_to(target)
        state["idx"] = state["scroll"] = 0
        return True

    if key in (curses.KEY_LEFT, ord("h")):
        parent = os.path.dirname(os.path.abspath(state["dir"]))
        state["dir"], state["entries"] = _navigate_to(parent)
        state["idx"] = state["scroll"] = 0
        return True

    if key == ord(" "):
        return state["dir"]

    return True  # unknown key or KEY_RESIZE


def _input_loop(stdscr, start_dir: str) -> str | None:
    """Main event loop for the directory picker."""
    import curses

    curses.curs_set(0)

    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_GREEN, -1)

    cur_dir, entries = _navigate_to(start_dir)
    state: dict = {
        "dir": cur_dir,
        "entries": entries,
        "idx": 0,
        "scroll": 0,
        "msg": "",
        "height": 1,
    }

    while True:
        max_y, _ = stdscr.getmaxyx()
        state["height"] = max(1, max_y - 7)

        _render(stdscr, state["dir"], state["entries"], state["idx"], state["scroll"], state["msg"])
        state["msg"] = ""

        try:
            key = stdscr.getch()
        except KeyboardInterrupt:
            return None

        result = _handle_key(key, state)
        if result is not True:
            return result


# ---------------------------------------------------------------------------
# 3. Public API
# ---------------------------------------------------------------------------


def pick_directory() -> str | None:
    """Launch an interactive directory picker and return the selected path.

    Returns ``None`` if the user cancels.
    """
    try:
        import curses
    except ImportError:
        print(
            "Error: curses is not available on this platform. Use --source to specify the path.",
            file=__import__("sys").stderr,
        )
        return None

    start = pick_start_directory()

    try:
        return curses.wrapper(_input_loop, start)
    except Exception:
        # Terminal too small or other curses error
        return None

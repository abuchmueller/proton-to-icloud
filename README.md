# proton-to-icloud

Migrate Proton Mail exports (`.eml` files) to iCloud Mail via IMAP.

## Why?

Proton Mail lets you export your mailbox as `.eml` files, but Apple Mail has no
reliable bulk-import for thousands of `.eml` files. Drag-and-drop silently drops
messages, `File > Import` creates a folder per file, and `.mbox` renaming is
rejected outright.

**proton-to-icloud** solves this by uploading `.eml` files directly to iCloud
via IMAP `APPEND`, preserving original dates, read/unread status, and folder
structure. It's a zero-dependency Python CLI tool that you can install globally
with `pipx` or `uv tool install`.

## Features

- **Direct IMAP upload** — bypasses Apple Mail entirely, talks to
  `imap.mail.me.com` over SSL
- **Preserves original dates** — parses the `Date:` header from each `.eml`
  and sets the IMAP internal date accordingly
- **Automatic resume** — saves progress to a state file every 100 messages;
  resumes where you left off after interruptions
- **Progress bar** — live terminal progress with ETA, throughput, and
  success/failure counts
- **Batch splitting** — optionally split thousands of `.eml` files into
  numbered batch folders
- **Zero dependencies** — stdlib only, no conflicts when installed globally
- **Folder routing** — automatically routes emails to the correct IMAP
  folders (Inbox, Sent, Archive, etc.) based on Proton metadata
- **Dry-run mode** — scan and count files without connecting or uploading

## Installation

### With `uv` (recommended)

```bash
uv tool install proton-to-icloud
```

### With `pipx`

```bash
pipx install proton-to-icloud
```

### From source

```bash
git clone https://github.com/abuchmueller/proton-to-icloud.git
cd proton-to-icloud
uv sync
uv run proton-to-icloud --help
```

## Prerequisites

1. **Export your Proton Mail** — use the [Proton Mail Export Tool](https://proton.me/support/proton-mail-export-tool)
   to download your mailbox as `.eml` files.

2. **Generate an App-Specific Password** — go to
   [appleid.apple.com](https://appleid.apple.com) → Sign-In and Security →
   App-Specific Passwords → Generate. This is required because iCloud IMAP
   does not accept your regular Apple ID password.

## Usage

The Proton Mail Export Tool creates a folder structure like:

```
your.address@pm.me/
└── mail_20260223_210229/
    ├── messageId1.eml
    ├── messageId1.metadata.json
    ├── messageId2.eml
    ├── messageId2.metadata.json
    └── ...
```

Point `--source` at the `mail_*` directory (the one containing the `.eml` files).
If you omit `--source`, an **interactive folder picker** launches — navigate with
arrow keys, Enter to open a directory, Space to select, Esc to cancel.

### Upload `.eml` files to iCloud

```bash
proton-to-icloud upload \
  --source "your.address@pm.me/mail_20260223_210229" \
  --mailbox "Proton-Import" \
  --email you@icloud.com
```

You'll be prompted securely for the app-specific password.

**Options:**

| Flag                  | Description                                    | Default          |
| --------------------- | ---------------------------------------------- | ---------------- |
| `-s`, `--source`      | Directory containing `.eml` files (recursive). Interactive picker when omitted. | *(picker)* |
| `-m`, `--mailbox`     | Base IMAP folder for subfolder routing and fallback | `Proton-Import`  |
| `-e`, `--email`       | Your iCloud / Apple ID email                   | *(required)*     |
| `-p`, `--password`    | App-specific password (prompted if omitted)    | *(prompted)*     |
| `--direct`            | Route into native iCloud folders instead of subfolders |            |
| `--dry-run`           | Scan only, don't connect or upload             |                  |
| `--resume-from N`     | Skip the first N files                         | `0`              |
| `--no-create-mailbox` | Don't auto-create the target folder            |                  |

### Folder Routing

When Proton metadata (`labels.json` and `.metadata.json` files) is present,
emails are automatically routed to the correct folders based on their Proton
labels.

**Default mode** — emails go into subfolders of `--mailbox`:

```bash
proton-to-icloud upload \
  --source "your.address@pm.me/mail_20260223_210229" \
  --email you@icloud.com
# → Proton-Import/Inbox, Proton-Import/Sent, Proton-Import/Archive, etc.
```

**Direct mode** (`--direct`) — emails go into native iCloud folders:

```bash
proton-to-icloud upload \
  --source "your.address@pm.me/mail_20260223_210229" \
  --email you@icloud.com \
  --direct
# → INBOX, Sent Messages, Archive, Junk, Deleted Messages, etc.
```

| Proton Label | Default mode (`--mailbox X`) | `--direct` mode |
|---|---|---|
| Inbox | `X/Inbox` | `INBOX` |
| Sent | `X/Sent` | `Sent Messages` |
| Drafts | `X/Drafts` | `Drafts` |
| Spam | `X/Spam` | `Junk` |
| Trash | `X/Trash` | `Deleted Messages` |
| Archive | `X/Archive` | `Archive` |
| Unknown / no metadata | `X` (fallback) | `X` (fallback) |

If no `labels.json` is found, all emails go to `--mailbox` (backward-compatible).

### Split `.eml` files into batch folders

If you prefer to drag-and-drop smaller batches into Apple Mail instead:

```bash
proton-to-icloud batch \
  --source "your.address@pm.me/mail_20260223_210229" \
  --batch-size 1000
```

**Options:**

| Flag              | Description                              | Default            |
| ----------------- | ---------------------------------------- | ------------------ |
| `-s`, `--source`  | Directory containing `.eml` files. Interactive picker when omitted. | *(picker)* |
| `-n`, `--batch-size` | Files per batch folder                | `1000`             |
| `-o`, `--output`  | Output directory for batch folders       | `<source>/batches` |
| `--move`          | Move files instead of copying            | copy               |

## Resume & Interruption Handling

The upload command saves a `.imap_upload_state.json` file inside the source
directory after every 100 messages. If the process is interrupted (Ctrl+C,
network drop, etc.), simply re-run the same command — it will detect the state
file and offer to resume.

You can also manually resume with `--resume-from N` to skip the first N files.

## Performance

In testing, throughput is approximately **1–2 seconds per message** depending on
file size and network conditions. For 50,000 messages, expect roughly 14–28
hours of upload time. An ethernet connection and a machine that can run
uninterrupted is recommended for large imports.

## Development

```bash
uv sync                        # Install deps
uv run pytest                  # Run tests
uv run ruff check src/ tests/  # Lint
uv run ruff format src/ tests/ # Format
```

## Claude Code Integration

If you use [Claude Code](https://claude.com/claude-code), this repo includes two project-level skills:

- **`/upload-guide`** — Interactive walkthrough that helps you construct the right upload command, choose routing modes, and handle long-running uploads.
- **`/diagnose-failures`** — Automated diagnostic workflow for investigating upload failures (header analysis, IMAP test matrix, root-cause suggestions).

These are optional — the CLI and this README are the primary interface.

## License

MIT — see [LICENSE](LICENSE).

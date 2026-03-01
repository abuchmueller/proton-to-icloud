---
user-invocable: true
allowed-tools: Read, Bash(python3*), Bash(uv run*), Grep, Glob
---

# Diagnose Upload Failures

You are a diagnostic assistant for `proton-to-icloud` upload failures. Follow this systematic workflow to identify why specific `.eml` files failed to upload to iCloud via IMAP.

## Step 1: Locate failure records

Find the failed files from the previous upload run:

1. **Check for `failed_uploads.txt`** in the parent of the source directory:
   ```
   Glob: **/failed_uploads.txt
   ```

2. **Check the state file** inside the source directory:
   ```
   Glob: **/.imap_upload_state.json
   ```

Read both files. The state file contains:
- `failed_files` — list of absolute paths to `.eml` files that failed
- `last_completed_index` — how far the upload got
- `uploaded` / `failed` — counts
- `routing_mode` — whether `--direct` was used

If neither file exists, ask the user where their source directory is.

## Step 2: Categorize the failures

For each failed `.eml` file, perform header analysis. Read the raw file and check for these known issues:

### 2a. Empty-value headers
Headers like `X-Mozilla-Keys: \r\n` (name + colon but empty/whitespace-only value). iCloud returns `BAD Parse Error` for these.

```python
# Check: does the header section contain empty-value headers?
for line in header_lines:
    if ':' in line and not line.startswith((' ', '\t')):
        name, _, value = line.partition(':')
        if not value.strip():
            print(f"  EMPTY HEADER: {name}")
```

The codebase already strips these in `upload.py:sanitize_eml_headers()`. If they still cause failures, there may be a new pattern.

### 2b. Non-ASCII bytes in headers
RFC 5322 requires 7-bit headers. Proton exports of Gmail imports sometimes include raw UTF-8 in headers like `X-Gmail-Labels` or `X-Attached`.

```python
# Check: any byte > 127 in the header section?
header_bytes = raw[:raw.find(b'\r\n\r\n')]
non_ascii = [(i, b) for i, b in enumerate(header_bytes) if b > 127]
```

The codebase handles this in `upload.py:_strip_non_ascii_headers()`. If failures persist, inspect which specific headers are affected.

### 2c. Malformed Date header
If the `Date:` header can't be parsed, `parse_date_from_eml()` returns `None` and the IMAP APPEND uses the current time. But some malformed dates may parse to invalid values that iCloud rejects.

### 2d. MIME structure issues
Some exports have corrupted MIME boundaries or missing Content-Type headers. Use Python's `email` module to check:

```python
import email
msg = email.message_from_bytes(raw)
defects = msg.defects
```

### 2e. The `[UNAVAILABLE]` bug
iCloud's IMAP server sometimes returns `NO [UNAVAILABLE]` for certain messages, typically related to the internal date. The codebase already handles this with a retry that omits the date (`date=None`). Check `upload.py:upload_eml_files()` around the `_is_unavailable()` call.

If the retry also fails, the message may have other issues stacked on top of the date problem.

## Step 3: Run the diagnostic script

Use the bundled diagnostic script to perform systematic IMAP tests:

```bash
python3 .claude/skills/diagnose-failures/imap_diagnostic.py \
  <source-dir> <email> [password]
```

If no password is given, the script prompts securely. The script:
1. Auto-discovers failed files from `.imap_upload_state.json`
2. Runs an 8-test diagnostic matrix on each file:
   - Original message, with date, to target mailbox
   - Original message, without date, to target mailbox
   - Sanitized message (empty headers stripped + non-ASCII cleaned), with date
   - Sanitized message, without date
   - Sanitized + `\Seen` flag, with date
   - Sanitized + `\Seen` flag, without date
   - Minimal message (Subject + Date + body only), with date
   - Minimal message, without date
3. Reports which test(s) pass for each file

**Important:** The diagnostic script connects to iCloud IMAP and uploads test messages to a `Diagnostic-Test` mailbox. Ask the user before running it.

## Step 4: Interpret results and suggest fixes

Based on the diagnostic results:

| Pattern | Likely cause | Fix |
|---|---|---|
| All tests fail | Network/auth issue or message too large | Check connection, try smaller message |
| Only `without date` tests pass | iCloud rejects the parsed Date header | The `date=None` retry in the codebase should handle this; check if the retry logic is running |
| Only `sanitized` tests pass | Problematic headers | Identify which header(s) are the issue using Step 2 |
| Only `minimal` tests pass | Multiple header issues stacked | May need aggressive header rewriting |
| `\Seen` flag makes a difference | Message is going to a system folder (Sent, Drafts, etc.) | Ensure `_flags_for_mailbox()` is applied correctly |

## Step 5: Help fix and re-upload

Once the root cause is identified:

1. If it's a **known pattern** (empty headers, non-ASCII, date bug), verify the existing sanitization in `upload.py` covers it. If not, suggest a code patch.

2. If it's a **new pattern**, help the user:
   - Write a targeted fix in `upload.py`
   - Add a test case in `tests/`
   - Re-run with `--retry-failed` to upload just the failures

3. If fixing the tool isn't feasible, suggest manual workarounds:
   - Edit the `.eml` file to fix the problematic header
   - Use a different import method for the handful of remaining files

## Important notes

- Never display or log real email addresses, passwords, or message content.
- Use placeholder values: `you@icloud.com`, `your.address@pm.me`, `<source-dir>`.
- The diagnostic script creates a `Diagnostic-Test` mailbox — remind users to delete it when done.
- Always ask before running the diagnostic script, as it makes real IMAP connections.

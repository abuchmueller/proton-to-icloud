"""Tests for proton_to_icloud.upload — pure-function tests only (no IMAP)."""

import os

import pytest

from proton_to_icloud.upload import (
    _flags_for_mailbox,
    _is_unavailable,
    _prepare_retry_files,
    _quote_mailbox,
    _strip_non_ascii_headers,
    collect_eml_files,
    load_state,
    parse_date_from_eml,
    sanitize_eml_headers,
    save_state,
)


class TestCollectEmlFiles:
    def test_finds_eml_files(self, tmp_path):
        (tmp_path / "a.eml").write_text("fake")
        (tmp_path / "b.eml").write_text("fake")
        (tmp_path / "c.json").write_text("fake")

        result = collect_eml_files(str(tmp_path))
        assert len(result) == 2
        assert all(f.endswith(".eml") for f in result)

    def test_recursive(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (tmp_path / "a.eml").write_text("fake")
        (sub / "b.eml").write_text("fake")

        result = collect_eml_files(str(tmp_path))
        assert len(result) == 2

    def test_excludes_directory(self, tmp_path):
        output = tmp_path / "batches"
        output.mkdir()
        (tmp_path / "a.eml").write_text("fake")
        (output / "b.eml").write_text("fake")

        result = collect_eml_files(str(tmp_path), exclude_dir=str(output))
        assert len(result) == 1
        assert result[0].endswith("a.eml")

    def test_empty_directory(self, tmp_path):
        result = collect_eml_files(str(tmp_path))
        assert result == []

    def test_sorted(self, tmp_path):
        (tmp_path / "z.eml").write_text("fake")
        (tmp_path / "a.eml").write_text("fake")
        (tmp_path / "m.eml").write_text("fake")

        result = collect_eml_files(str(tmp_path))
        basenames = [os.path.basename(f) for f in result]
        assert basenames == ["a.eml", "m.eml", "z.eml"]

    def test_case_insensitive_extension(self, tmp_path):
        (tmp_path / "a.EML").write_text("fake")
        (tmp_path / "b.Eml").write_text("fake")

        result = collect_eml_files(str(tmp_path))
        assert len(result) == 2


class TestParseDateFromEml:
    def test_valid_date(self):
        raw = b"From: test@test.com\nDate: Mon, 15 Jan 2024 10:30:45 +0000\nSubject: Test\n\nBody"
        result = parse_date_from_eml(raw)
        assert result is not None
        assert "2024" in result

    def test_no_date_header(self):
        raw = b"From: test@test.com\nSubject: No date\n\nBody"
        result = parse_date_from_eml(raw)
        assert result is None

    def test_malformed_date(self):
        raw = b"Date: not-a-real-date\n\nBody"
        result = parse_date_from_eml(raw)
        assert result is None

    def test_empty_bytes(self):
        result = parse_date_from_eml(b"")
        assert result is None


class TestSaveStateRoutingMode:
    def test_default_routing_mode_is_single(self, tmp_path):
        save_state(str(tmp_path), 0, 1, 0, [], "INBOX")
        state = load_state(str(tmp_path))
        assert state is not None
        assert state["routing_mode"] == "single"

    def test_direct_routing_mode(self, tmp_path):
        save_state(str(tmp_path), 0, 1, 0, [], "INBOX", routing_mode="direct")
        state = load_state(str(tmp_path))
        assert state is not None
        assert state["routing_mode"] == "direct"

    def test_routed_routing_mode(self, tmp_path):
        save_state(str(tmp_path), 0, 1, 0, [], "INBOX", routing_mode="routed")
        state = load_state(str(tmp_path))
        assert state is not None
        assert state["routing_mode"] == "routed"


class TestSanitizeEmlHeaders:
    def test_strips_empty_value_header_lf(self):
        raw = b"From: a@b.com\nX-Mozilla-Keys: \nDate: Mon, 1 Jan 2024\n\nBody"
        result = sanitize_eml_headers(raw)
        assert b"X-Mozilla-Keys" not in result
        assert b"From: a@b.com" in result
        assert b"Date: Mon, 1 Jan 2024" in result
        assert result.endswith(b"\n\nBody")

    def test_strips_empty_value_header_crlf(self):
        raw = b"From: a@b.com\r\nX-Mozilla-Keys: \r\nDate: Mon, 1 Jan 2024\r\n\r\nBody"
        result = sanitize_eml_headers(raw)
        assert b"X-Mozilla-Keys" not in result
        assert b"From: a@b.com" in result
        assert result.endswith(b"\r\n\r\nBody")

    def test_strips_header_with_no_space_after_colon(self):
        raw = b"From: a@b.com\nX-Empty:\nSubject: Hi\n\nBody"
        result = sanitize_eml_headers(raw)
        assert b"X-Empty" not in result
        assert b"Subject: Hi" in result

    def test_preserves_headers_with_values(self):
        raw = b"From: a@b.com\nX-Custom: value\nSubject: Hi\n\nBody"
        result = sanitize_eml_headers(raw)
        assert result == raw

    def test_preserves_body_unchanged(self):
        body = b"Body line with X-Header: \nand more"
        raw = b"From: a@b.com\nX-Empty: \n\n" + body
        result = sanitize_eml_headers(raw)
        assert result.endswith(b"\n\n" + body)

    def test_preserves_folded_continuation_lines(self):
        raw = b"Subject: long\n value here\nFrom: a@b.com\n\nBody"
        result = sanitize_eml_headers(raw)
        assert b" value here" in result

    def test_no_body_separator_returns_unchanged(self):
        raw = b"From: a@b.com\nX-Empty: "
        result = sanitize_eml_headers(raw)
        assert result == raw

    def test_multiple_empty_headers(self):
        raw = b"From: a@b.com\nX-A: \nX-B: \nSubject: Hi\n\nBody"
        result = sanitize_eml_headers(raw)
        assert b"X-A" not in result
        assert b"X-B" not in result
        assert b"Subject: Hi" in result


class TestPrepareRetryFiles:
    def test_loads_failed_files_from_state(self, tmp_path):
        """Happy path: state has failed files that exist on disk."""
        eml_a = tmp_path / "a.eml"
        eml_b = tmp_path / "b.eml"
        eml_a.write_text("fake")
        eml_b.write_text("fake")

        save_state(
            str(tmp_path),
            99,
            98,
            2,
            [str(eml_a), str(eml_b)],
            "Proton-Import",
            routing_mode="single",
        )

        files, routing, mode = _prepare_retry_files(
            str(tmp_path), direct=False, base_mailbox="Proton-Import"
        )
        assert len(files) == 2
        assert str(eml_a) in files
        assert str(eml_b) in files
        assert mode == "single"
        # All files should appear in the routing plan
        all_routed = [p for paths in routing.values() for p in paths]
        assert set(all_routed) == {str(eml_a), str(eml_b)}

    def test_exits_when_no_state_file(self, tmp_path):
        """No state file → exit with code 1."""
        with pytest.raises(SystemExit, match="1"):
            _prepare_retry_files(str(tmp_path), direct=False, base_mailbox="INBOX")

    def test_exits_when_failed_files_empty(self, tmp_path):
        """State exists but failed_files is empty → exit with code 0."""
        save_state(str(tmp_path), 99, 100, 0, [], "INBOX", routing_mode="single")

        with pytest.raises(SystemExit, match="0"):
            _prepare_retry_files(str(tmp_path), direct=False, base_mailbox="INBOX")

    def test_skips_missing_files_with_warning(self, tmp_path, capsys):
        """Files that no longer exist on disk are skipped with a warning."""
        eml_exists = tmp_path / "exists.eml"
        eml_exists.write_text("fake")
        missing_path = str(tmp_path / "gone.eml")

        save_state(
            str(tmp_path),
            99,
            98,
            2,
            [str(eml_exists), missing_path],
            "INBOX",
            routing_mode="single",
        )

        files, _routing, _mode = _prepare_retry_files(
            str(tmp_path), direct=False, base_mailbox="INBOX"
        )
        assert len(files) == 1
        assert str(eml_exists) in files
        captured = capsys.readouterr()
        assert "gone.eml" in captured.out

    def test_exits_when_all_files_missing(self, tmp_path):
        """All failed files removed from disk → exit with code 0."""
        save_state(
            str(tmp_path),
            99,
            98,
            2,
            [str(tmp_path / "a.eml"), str(tmp_path / "b.eml")],
            "INBOX",
            routing_mode="single",
        )

        with pytest.raises(SystemExit, match="0"):
            _prepare_retry_files(str(tmp_path), direct=False, base_mailbox="INBOX")

    def test_validates_routing_mode_mismatch(self, tmp_path):
        """Saved direct mode + current non-direct flags → exit with code 1."""
        eml = tmp_path / "a.eml"
        eml.write_text("fake")
        save_state(
            str(tmp_path),
            99,
            99,
            1,
            [str(eml)],
            "INBOX",
            routing_mode="direct",
        )

        with pytest.raises(SystemExit, match="1"):
            _prepare_retry_files(str(tmp_path), direct=False, base_mailbox="INBOX")

    def test_validates_routing_mode_mismatch_reverse(self, tmp_path):
        """Saved non-direct mode + current --direct flag → exit with code 1."""
        eml = tmp_path / "a.eml"
        eml.write_text("fake")
        save_state(
            str(tmp_path),
            99,
            99,
            1,
            [str(eml)],
            "INBOX",
            routing_mode="single",
        )

        with pytest.raises(SystemExit, match="1"):
            _prepare_retry_files(str(tmp_path), direct=True, base_mailbox="INBOX")


class TestQuoteMailbox:
    """imaplib does not quote mailbox names; we must do it ourselves."""

    def test_quotes_name_with_space(self):
        assert _quote_mailbox("Sent Messages") == '"Sent Messages"'

    def test_quotes_name_with_multiple_spaces(self):
        assert _quote_mailbox("Deleted Messages") == '"Deleted Messages"'

    def test_no_quotes_for_simple_name(self):
        assert _quote_mailbox("INBOX") == "INBOX"

    def test_no_quotes_for_path_without_spaces(self):
        assert _quote_mailbox("Proton-Import/Sent") == "Proton-Import/Sent"

    def test_quotes_subfolder_with_space(self):
        assert _quote_mailbox("Proton Import/Sent") == '"Proton Import/Sent"'


class TestFlagsForMailbox:
    def test_seen_for_sent_messages(self):
        assert _flags_for_mailbox("Sent Messages") == r"\Seen"

    def test_seen_for_drafts(self):
        assert _flags_for_mailbox("Drafts") == r"\Seen"

    def test_seen_for_deleted_messages(self):
        assert _flags_for_mailbox("Deleted Messages") == r"\Seen"

    def test_seen_for_junk(self):
        assert _flags_for_mailbox("Junk") == r"\Seen"

    def test_no_flags_for_inbox(self):
        assert _flags_for_mailbox("INBOX") == ""

    def test_no_flags_for_archive(self):
        assert _flags_for_mailbox("Archive") == ""

    def test_no_flags_for_custom_folder(self):
        assert _flags_for_mailbox("Proton-Import") == ""


class TestStripNonAsciiHeaders:
    def test_replaces_non_ascii_in_headers(self):
        raw = b"X-Label: Ge\xc3\xb6ffnet\r\nFrom: a@b.com\r\n\r\nBody"
        result = _strip_non_ascii_headers(raw)
        assert b"\xc3" not in result[: result.find(b"\r\n\r\n")]
        assert result.endswith(b"\r\n\r\nBody")

    def test_preserves_ascii_only_headers(self):
        raw = b"From: a@b.com\r\nSubject: Hello\r\n\r\nBody"
        assert _strip_non_ascii_headers(raw) == raw

    def test_preserves_non_ascii_in_body(self):
        raw = b"From: a@b.com\r\n\r\nBody with \xc3\xb6 umlaut"
        result = _strip_non_ascii_headers(raw)
        assert b"\xc3\xb6" in result

    def test_no_body_separator_returns_unchanged(self):
        raw = b"From: a@b.com\r\nX-Label: \xc3\xb6"
        assert _strip_non_ascii_headers(raw) == raw


class TestIsUnavailable:
    def test_detects_bytes_response(self):
        assert _is_unavailable([b"[UNAVAILABLE] Unexpected exception (took 357 ms)"])

    def test_detects_string_response(self):
        assert _is_unavailable(["[UNAVAILABLE] Unexpected exception"])

    def test_ignores_other_errors(self):
        assert not _is_unavailable([b"[TRYCREATE] Mailbox does not exist"])

    def test_empty_response(self):
        assert not _is_unavailable([])

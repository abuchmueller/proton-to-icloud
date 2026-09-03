"""Tests for proton_to_icloud.msgid — Message-ID matching, no IMAP."""

from proton_to_icloud.msgid import (
    classify_eml_files,
    decode_imap_utf7,
    extract_message_id_from_eml,
    normalize_message_id,
    parse_imap_list_line,
    parse_message_ids_from_fetch,
)


class TestNormalizeMessageId:
    def test_strips_brackets_and_lowercases(self):
        assert normalize_message_id("<ABC@Example.COM>") == "abc@example.com"

    def test_already_bare(self):
        assert normalize_message_id("abc@example.com") == "abc@example.com"

    def test_collapses_whitespace(self):
        assert normalize_message_id("  <id@host>  ") == "id@host"

    def test_none_and_empty(self):
        assert normalize_message_id(None) is None
        assert normalize_message_id("") is None
        assert normalize_message_id("   ") is None
        assert normalize_message_id("<>") is None


class TestExtractMessageIdFromEml:
    def test_standard_header(self):
        raw = b"From: a@b.com\nMessage-ID: <one@host>\nSubject: Hi\n\nBody"
        assert extract_message_id_from_eml(raw) == "one@host"

    def test_case_insensitive_header_name(self):
        raw = b"From: a@b.com\nMessage-Id: <two@host>\n\nBody"
        assert extract_message_id_from_eml(raw) == "two@host"

    def test_folded_header(self):
        raw = b"From: a@b.com\nMessage-ID:\n <folded@host>\n\nBody"
        assert extract_message_id_from_eml(raw) == "folded@host"

    def test_missing_header(self):
        raw = b"From: a@b.com\nSubject: Hi\n\nBody"
        assert extract_message_id_from_eml(raw) is None

    def test_header_fragment_from_imap_fetch(self):
        raw = b"Message-ID: <imap@host>\r\n\r\n"
        assert extract_message_id_from_eml(raw) == "imap@host"


class TestDecodeImapUtf7:
    def test_ascii_unchanged(self):
        assert decode_imap_utf7("INBOX") == "INBOX"

    def test_literal_ampersand(self):
        assert decode_imap_utf7("A&-B") == "A&B"

    def test_non_ascii(self):
        # ö is U+00F6 → modified UTF-7 &APY-
        assert decode_imap_utf7("&APY-") == "ö"


class TestParseImapListLine:
    def test_inbox_unquoted(self):
        flags, name = parse_imap_list_line('(\\HasNoChildren) "/" INBOX')
        assert name == "INBOX"
        assert "\\HasNoChildren" in flags

    def test_quoted_with_space(self):
        flags, name = parse_imap_list_line('(\\HasNoChildren \\Sent) "/" "Sent Messages"')
        assert name == "Sent Messages"
        assert "\\Sent" in flags

    def test_nested_folder(self):
        _flags, name = parse_imap_list_line('(\\HasNoChildren) "/" "Proton-Import/Inbox"')
        assert name == "Proton-Import/Inbox"

    def test_noselect(self):
        flags, name = parse_imap_list_line('(\\Noselect \\HasChildren) "/" "[Mail]"')
        assert name == "[Mail]"
        assert "\\Noselect" in flags

    def test_garbage(self):
        assert parse_imap_list_line("not a list line") is None


class TestParseMessageIdsFromFetch:
    def test_tuple_payloads(self):
        fetched = [
            (b"1 (UID 12 BODY[HEADER.FIELDS (MESSAGE-ID)] {23}", b"Message-ID: <a@h>\r\n\r\n"),
            b")",
            (b"2 (UID 13 BODY[HEADER.FIELDS (MESSAGE-ID)] {23}", b"Message-ID: <B@H>\r\n\r\n"),
            b")",
        ]
        assert parse_message_ids_from_fetch(fetched) == {"a@h", "b@h"}

    def test_empty_and_none(self):
        assert parse_message_ids_from_fetch(None) == set()
        assert parse_message_ids_from_fetch([]) == set()


class TestClassifyEmlFiles:
    def _write(self, path, msgid: str | None):
        if msgid is None:
            path.write_bytes(b"From: a@b.com\nSubject: Hi\n\nBody")
        else:
            path.write_bytes(f"From: a@b.com\nMessage-ID: <{msgid}>\nSubject: Hi\n\nBody".encode())

    def test_skips_existing_and_intra_export_dupes(self, tmp_path):
        a = tmp_path / "a.eml"
        b = tmp_path / "b.eml"
        c = tmp_path / "c.eml"
        d = tmp_path / "d.eml"
        self._write(a, "already@host")
        self._write(b, "new@host")
        self._write(c, "new@host")  # duplicate of b
        self._write(d, None)

        upload, skip, missing = classify_eml_files(
            [str(a), str(b), str(c), str(d)],
            existing_ids={"already@host"},
        )
        assert skip == 2  # a (in iCloud) + c (dup of b)
        assert missing == 1
        assert upload == 2  # b and d

    def test_all_new(self, tmp_path):
        a = tmp_path / "a.eml"
        self._write(a, "only@host")
        upload, skip, missing = classify_eml_files([str(a)], existing_ids=set())
        assert (upload, skip, missing) == (1, 0, 0)
